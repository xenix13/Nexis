"""Builds a demo media library (fictional titles only) for the documentation screenshots.

python3 tools/screenshots/demo_library.py <folder>

Everything is generated locally: posters and backdrops drawn with Pillow, Kodi-style .nfo files for the
metadata (no TMDB key needed), and videos made by ffmpeg's built-in sources (fractals, cellular automata,
gradients). A short clip is encoded once per title, then looped without re-encoding to reach a feature length.
"""
import json
import math
import os
import random
import subprocess
import sys
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = "/usr/share/fonts/opentype/inter"


def font(name, size):
    for p in (os.path.join(FONT_DIR, name), "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def gradient(w, h, top, bottom):
    img = Image.new("RGB", (w, h))
    a, b = hexrgb(top), hexrgb(bottom)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    return img


# ---------- scenes: one drawing function per mood, at any size ----------

def scene_sunset(w, h, pal, rnd):
    img = gradient(w, h, pal[0], pal[1])
    d = ImageDraw.Draw(img, "RGBA")
    r = int(min(w, h) * 0.18)
    cx, cy = int(w * rnd.uniform(0.35, 0.65)), int(h * 0.52)
    for k in range(6, 0, -1):
        d.ellipse([cx - r - k * 18, cy - r - k * 18, cx + r + k * 18, cy + r + k * 18], fill=hexrgb(pal[2]) + (14,))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexrgb(pal[2]))
    for layer in range(4):
        base = h * (0.6 + layer * 0.1)
        col = hexrgb(pal[3 + layer % 2]) + (255,)
        pts = [(0, h)]
        amp, freq, ph = h * (0.08 - layer * 0.012), rnd.uniform(1.5, 3.5), rnd.uniform(0, 6)
        for x in range(0, w + 20, 20):
            pts.append((x, base - amp * (0.6 + 0.4 * math.sin(x / w * freq * math.pi + ph)) * abs(math.sin(x / w * 7 + ph))))
        pts.append((w, h))
        d.polygon(pts, fill=col)
    return img


def scene_city(w, h, pal, rnd):
    img = gradient(w, h, pal[0], pal[1])
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(int(w * h / 9000)):
        x, y = rnd.randrange(w), rnd.randrange(int(h * 0.5))
        d.point((x, y), fill=(255, 255, 255, rnd.randrange(60, 200)))
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rectangle([0, int(h * 0.55), w, int(h * 0.58)], fill=hexrgb(pal[2]) + (160,))
    img.paste(glow.filter(ImageFilter.GaussianBlur(h // 30)), (0, 0), glow.filter(ImageFilter.GaussianBlur(h // 30)))
    x = 0
    while x < w:
        bw = rnd.randint(w // 22, w // 9)
        bh = rnd.randint(int(h * 0.2), int(h * 0.62))
        top = h - bh
        d.rectangle([x, top, x + bw, h], fill=hexrgb(pal[3]))
        for wy in range(top + 10, h - 6, max(h // 60, 8)):
            for wx in range(x + 6, x + bw - 6, max(w // 90, 7)):
                if rnd.random() < 0.28:
                    c = hexrgb(pal[2 if rnd.random() < 0.6 else 4])
                    d.rectangle([wx, wy, wx + max(w // 300, 2), wy + max(h // 200, 3)], fill=c + (220,))
        x += bw + rnd.randint(0, w // 60)
    d.line([(0, int(h * 0.97)), (w, int(h * 0.97))], fill=hexrgb(pal[4]) + (200,), width=max(h // 150, 2))
    return img


def scene_space(w, h, pal, rnd):
    img = gradient(w, h, pal[0], pal[1])
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(int(w * h / 2500)):
        x, y = rnd.randrange(w), rnd.randrange(h)
        s = rnd.choice((1, 1, 1, 2))
        d.ellipse([x, y, x + s, y + s], fill=(255, 255, 255, rnd.randrange(80, 255)))
    neb = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    nd = ImageDraw.Draw(neb)
    for _ in range(7):
        r = rnd.randint(min(w, h) // 6, min(w, h) // 2)
        x, y = rnd.randrange(w), rnd.randrange(h)
        nd.ellipse([x - r, y - r, x + r, y + r], fill=hexrgb(rnd.choice(pal[2:4])) + (40,))
    neb = neb.filter(ImageFilter.GaussianBlur(min(w, h) // 10))
    img.paste(neb, (0, 0), neb)
    r = int(min(w, h) * 0.22)
    cx, cy = int(w * rnd.uniform(0.3, 0.7)), int(h * rnd.uniform(0.35, 0.55))
    planet = gradient(2 * r, 2 * r, pal[4], pal[3])
    mask = Image.new("L", (2 * r, 2 * r), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, 2 * r - 1, 2 * r - 1], fill=255)
    img.paste(planet, (cx - r, cy - r), mask)
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([cx - int(r * 1.8), cy - int(r * 0.32), cx + int(r * 1.8), cy + int(r * 0.32)],
              outline=hexrgb(pal[2]) + (210,), width=max(r // 18, 3))
    return img


def scene_ocean(w, h, pal, rnd):
    img = gradient(w, h, pal[0], pal[1])
    d = ImageDraw.Draw(img, "RGBA")
    r = int(min(w, h) * 0.1)
    cx, cy = int(w * 0.72), int(h * 0.25)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexrgb(pal[2]) + (230,))
    for i in range(14):
        base = h * (0.45 + i * 0.04)
        col = hexrgb(pal[3]) if i % 2 else hexrgb(pal[4])
        pts = [(0, h)]
        ph = rnd.uniform(0, 6)
        for x in range(0, w + 10, 10):
            pts.append((x, base + math.sin(x / w * 9 + ph) * h * 0.012 * (1 + i * 0.15)))
        pts.append((w, h))
        d.polygon(pts, fill=col + (110 + i * 10,))
    lx = int(w * 0.25)
    d.rectangle([lx - w // 60, int(h * 0.3), lx + w // 60, int(h * 0.5)], fill=(235, 235, 235, 255))
    d.polygon([(lx - w // 40, int(h * 0.3)), (lx + w // 40, int(h * 0.3)), (lx, int(h * 0.26))], fill=hexrgb(pal[2]))
    beam = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(beam).polygon([(lx, int(h * 0.31)), (w, int(h * 0.18)), (w, int(h * 0.36))], fill=hexrgb(pal[2]) + (60,))
    beam = beam.filter(ImageFilter.GaussianBlur(h // 80))
    img.paste(beam, (0, 0), beam)
    return img


def scene_forest(w, h, pal, rnd):
    img = gradient(w, h, pal[0], pal[1])
    d = ImageDraw.Draw(img, "RGBA")
    r = int(min(w, h) * 0.09)
    d.ellipse([int(w * 0.6) - r, int(h * 0.2) - r, int(w * 0.6) + r, int(h * 0.2) + r], fill=hexrgb(pal[2]) + (220,))
    for layer in range(5):
        col = hexrgb(pal[3 + (layer % 2)])
        shade = 70 + layer * 45
        base = h * (0.55 + layer * 0.1)
        for _ in range(int(w / (40 + layer * 12)) + 4):
            x = rnd.randrange(-40, w + 40)
            th = rnd.randint(int(h * 0.15), int(h * 0.32)) * (1 + layer * 0.25)
            tw = th * 0.32
            d.polygon([(x - tw / 2, base), (x + tw / 2, base), (x, base - th)], fill=col + (min(shade, 255),))
        d.rectangle([0, base - 2, w, h], fill=col + (min(shade, 255),))
    fog = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(fog).rectangle([0, int(h * 0.62), w, int(h * 0.72)], fill=(255, 255, 255, 50))
    fog = fog.filter(ImageFilter.GaussianBlur(h // 25))
    img.paste(fog, (0, 0), fog)
    return img


def scene_bubbles(w, h, pal, rnd):
    img = gradient(w, h, pal[0], pal[1])
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(26):
        r = rnd.randint(min(w, h) // 20, min(w, h) // 4)
        x, y = rnd.randrange(w), rnd.randrange(h)
        d.ellipse([x - r, y - r, x + r, y + r], fill=hexrgb(rnd.choice(pal[2:])) + (rnd.randint(50, 130),))
    layer = layer.filter(ImageFilter.GaussianBlur(min(w, h) // 120))
    img.paste(layer, (0, 0), layer)
    return img


SCENES = {"sunset": scene_sunset, "city": scene_city, "space": scene_space, "ocean": scene_ocean,
          "forest": scene_forest, "bubbles": scene_bubbles}


def poster(t, path, w=600, h=900):
    rnd = random.Random(t["title"])
    img = SCENES[t["scene"]](w, h, t["palette"], rnd).convert("RGBA")
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for y in range(int(h * 0.55), h):
        sd.line([(0, y), (w, y)], fill=(0, 0, 0, int(210 * (y - h * 0.55) / (h * 0.45))))
    img = Image.alpha_composite(img, shade)
    d = ImageDraw.Draw(img)
    words = t["title"].upper().split()
    lines, cur = [], ""
    f = font("InterDisplay-Black.otf", 74)
    for wd in words:
        test = (cur + " " + wd).strip()
        if d.textlength(test, font=f) > w - 70 and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = test
    lines.append(cur)
    size = 74
    while any(d.textlength(ln, font=f) > w - 60 for ln in lines) and size > 36:
        size -= 4
        f = font("InterDisplay-Black.otf", size)
    y = h - 80 - len(lines) * (size + 6)
    for ln in lines:
        d.text(((w - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=(255, 255, 255))
        y += size + 6
    small = font("Inter-SemiBold.otf", 20)
    tag = t.get("poster_line", "").upper()
    if tag:
        d.text(((w - d.textlength(tag, font=small)) / 2, h - 58), tag, font=small, fill=hexrgb(t["palette"][2]))
    img.convert("RGB").save(path, quality=90)


def backdrop(t, path, w=1920, h=1080, seed=""):
    rnd = random.Random(t["title"] + seed)
    SCENES[t["scene"]](w, h, t["palette"], rnd).save(path, quality=88)


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


SOURCES = [
    "mandelbrot=s=640x360:r=1:maxiter=300:start_scale=2.5:end_scale=0.4",
    "gradients=s=640x360:r=1:speed=0.015:n=3:type=circular",
    "mandelbrot=s=640x360:r=1:maxiter=250:start_x=-0.7453:start_y=0.1127:start_scale=0.05:end_scale=0.002",
    "gradients=s=640x360:r=1:speed=0.01:n=4",
    "mandelbrot=s=640x360:r=1:maxiter=200:start_x=-0.743643887:start_y=-0.131825904:start_scale=0.01:end_scale=0.00005",
    "gradients=s=640x360:r=1:speed=0.02:n=6:type=radial",
]


def video(path, src_index, minutes, audio_langs=("eng",), chapters=None):
    """Encodes a 90 s clip from a lavfi source, then loops it (stream copy) up to `minutes`."""
    clip = path + ".clip.mkv"
    src = SOURCES[src_index % len(SOURCES)]
    args = ["-f", "lavfi", "-t", "90", "-i", src.split(",")[0]]
    vf = ",".join(src.split(",")[1:]) or "null"
    for _ in audio_langs:
        args += ["-f", "lavfi", "-t", "90", "-i", "anullsrc=r=48000:cl=stereo"]
    maps = ["-map", "0:v"] + sum((["-map", f"{i + 1}:a"] for i in range(len(audio_langs))), [])
    meta = sum(([f"-metadata:s:a:{i}", f"language={lang}"] for i, lang in enumerate(audio_langs)), [])
    ff(*args, *maps, "-t", "90", "-vf", vf + ",scale=1920:1080:flags=bicubic,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "40", "-g", "10", "-c:a", "aac", "-ac", "2", "-b:a", "24k", *meta, clip)
    loops = max(int(minutes * 60 / 90), 1)
    meta_file = None
    extra = []
    if chapters:
        meta_file = path + ".chapters.txt"
        with open(meta_file, "w") as f:
            f.write(";FFMETADATA1\n")
            for i, (start, title) in enumerate(chapters):
                end = chapters[i + 1][0] if i + 1 < len(chapters) else minutes * 60
                f.write(f"[CHAPTER]\nTIMEBASE=1/1\nSTART={start}\nEND={end}\ntitle={title}\n")
        extra = ["-i", meta_file, "-map_chapters", "1"]
    ff("-stream_loop", str(loops - 1), "-i", clip, *extra, "-map", "0", "-c", "copy", "-t", str(minutes * 60), path)
    os.remove(clip)
    if meta_file:
        os.remove(meta_file)


def srt(path, lines):
    with open(path, "w") as f:
        for i, (a, b, text) in enumerate(lines, 1):
            f.write(f"{i}\n00:00:{a:02d},000 --> 00:00:{b:02d},000\n{text}\n\n")


def nfo(path, root, fields):
    out = [f"<?xml version='1.0' encoding='utf-8'?>", f"<{root}>"]
    for k, v in fields:
        if isinstance(v, dict):
            out.append(f"  <{k}>" + "".join(f"<{a}>{escape(str(b))}</{a}>" for a, b in v.items()) + f"</{k}>")
        else:
            out.append(f"  <{k}>{escape(str(v))}</{k}>")
    out.append(f"</{root}>")
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")


HERE = os.path.dirname(os.path.abspath(__file__))


def build(root):
    data = json.load(open(os.path.join(HERE, "demo_titles.json")))
    films = os.path.join(root, "Movies")
    shows = os.path.join(root, "Shows")
    photos = os.path.join(root, "Photos")
    for d in (films, shows, photos):
        os.makedirs(d, exist_ok=True)
    for i, m in enumerate(data["movies"]):
        name = f"{m['title']} ({m['year']})"
        d = os.path.join(films, name)
        os.makedirs(d, exist_ok=True)
        poster(m, os.path.join(d, "poster.jpg"))
        backdrop(m, os.path.join(d, "fanart.jpg"))
        fields = [("title", m["title"]), ("year", m["year"]), ("plot", m["plot"]), ("tagline", m.get("tagline", "")),
                  ("rating", m["rating"]), ("mpaa", m.get("mpaa", "PG-13")), ("studio", m.get("studio", "Lantern Pictures")),
                  ("runtime", m["minutes"])]
        fields += [("genre", g) for g in m["genres"]]
        fields += [("director", p) for p in m.get("directors", [])]
        fields += [("actor", {"name": a, "role": r}) for a, r in m.get("cast", [])]
        nfo(os.path.join(d, "movie.nfo"), "movie", fields)
        v = os.path.join(d, name + ".mkv")
        if not os.path.exists(v):
            chapters = [(0, "Opening"), (300, "Chapter 1"), (m["minutes"] * 20, "Chapter 2"),
                        (m["minutes"] * 40, "Chapter 3"), (m["minutes"] * 55, "End Credits")]
            video(v, i, m["minutes"], audio_langs=tuple(m.get("audio", ["eng"])), chapters=chapters)
        for lang, label in (("en", "Hello? Is anyone out there?"), ("fr", "Allô ? Il y a quelqu'un ?")):
            srt(os.path.join(d, f"{name}.{lang}.srt"), [(2, 6, label), (8, 12, "…")])
        print("movie:", name, flush=True)
    for si, s in enumerate(data["shows"]):
        d = os.path.join(shows, f"{s['title']} ({s['year']})")
        os.makedirs(d, exist_ok=True)
        poster(s, os.path.join(d, "poster.jpg"))
        backdrop(s, os.path.join(d, "fanart.jpg"))
        fields = [("title", s["title"]), ("year", s["year"]), ("plot", s["plot"]), ("rating", s["rating"]),
                  ("mpaa", s.get("mpaa", "TV-14")), ("studio", s.get("studio", "Harborline TV")),
                  ("premiered", f"{s['year']}-03-14")]
        fields += [("genre", g) for g in s["genres"]]
        fields += [("actor", {"name": a, "role": r}) for a, r in s.get("cast", [])]
        nfo(os.path.join(d, "tvshow.nfo"), "tvshow", fields)
        for season, eps in enumerate(s["seasons"], 1):
            sd = os.path.join(d, f"Season {season:02d}")
            os.makedirs(sd, exist_ok=True)
            for e, (title, plot) in enumerate(eps, 1):
                base = os.path.join(sd, f"{s['title']} - S{season:02d}E{e:02d} - {title}")
                if not os.path.exists(base + ".mkv"):
                    chapters = [(0, "Previously"), (60, "Opening"), (150, "Part 1"), (s["minutes"] * 30, "Part 2"),
                                (s["minutes"] * 60 - 90, "Ending")]
                    video(base + ".mkv", si * 7 + season * 3 + e, s["minutes"], chapters=chapters)
                nfo(base + ".nfo", "episodedetails", [("title", title), ("season", season), ("episode", e),
                                                      ("plot", plot), ("aired", f"{s['year'] + season - 1}-03-{10 + e:02d}")])
        print("show:", s["title"], flush=True)
    for album, items in data["photos"].items():
        ad = os.path.join(photos, album)
        os.makedirs(ad, exist_ok=True)
        for k, (scene, pal, date) in enumerate(items):
            p = os.path.join(ad, f"IMG_{2000 + k}.jpg")
            rnd = random.Random(album + str(k))
            img = SCENES[scene](1800, 1200, pal, rnd)
            exif = Image.Exif()
            exif[0x0132] = date
            exif[0x0110] = "Demo Camera X1"
            ifd = exif.get_ifd(0x8769)
            ifd[0x9003] = date
            img.save(p, quality=85, exif=exif)
        print("album:", album, flush=True)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "demo-media")
