"""A small fake Plex Media Server used by the tests (and for UI screenshots)."""
import http.server
import io
import json
import os
import socketserver
import subprocess
import tempfile
import threading
import urllib.parse

TOKEN = "fake-admin-token-XYZ"


def ep(rk, show, season, idx, section, title, dur=1_440_000, audio=None, subs=None):
    return {
        "ratingKey": str(rk), "key": f"/library/metadata/{rk}", "type": "episode", "title": title,
        "grandparentTitle": show["title"], "grandparentRatingKey": show["ratingKey"],
        "grandparentThumb": show["thumb"], "grandparentArt": show["art"],
        "parentRatingKey": season, "parentIndex": int(str(season)[-1]), "index": idx,
        "thumb": f"/library/metadata/{rk}/thumb/1700000000", "duration": dur,
        "summary": f"Résumé de l'épisode {idx}.", "librarySectionID": section,
        "originallyAvailableAt": "2021-03-0%d" % idx,
        "Media": [{"id": int(rk) * 10, "videoResolution": "1080", "videoCodec": "h264", "audioCodec": "aac",
                   "container": "mkv", "bitrate": 4000,
                   "Part": [{"id": int(rk) * 100, "key": f"/library/parts/{int(rk)*100}/1/file.mkv",
                             "Stream": [{"id": int(rk) * 1000 + 1, "streamType": 1, "codec": "h264"}]
                             + (audio or [{"id": int(rk) * 1000 + 2, "streamType": 2, "codec": "aac",
                                           "languageCode": "fra", "displayTitle": "Français (AAC Stéréo)",
                                           "selected": True}])
                             + (subs or [])}]}],
    }


def build_library():
    movies = []
    titles = [("Le Grand Voyage", 2019), ("Nuit Polaire", 2021), ("Les Horizons", 2018),
              ("Brume", 2022), ("Rive Gauche", 2017), ("Station Zéro", 2023)]
    for i, (t, y) in enumerate(titles):
        rk = 101 + i
        movies.append({
            "ratingKey": str(rk), "key": f"/library/metadata/{rk}", "type": "movie", "title": t, "year": y,
            "thumb": f"/library/metadata/{rk}/thumb/1700000000", "art": f"/library/metadata/{rk}/art/1700000000",
            "duration": 6_000_000 + i * 300_000, "summary": f"{t} : un film de démonstration pour Séance.",
            "tagline": "Une histoire inventée.", "audienceRating": 7.0 + i / 10, "contentRating": "Tous publics",
            "addedAt": 1_700_000_000 + i * 1000, "librarySectionID": 1, "studio": "Studio Démo",
            "Genre": [{"tag": "Drame"}, {"tag": "Aventure"}], "Director": [{"tag": "Réalisateur Fictif"}],
            "Role": [{"tag": "Acteur Un", "role": "Héros"}, {"tag": "Actrice Deux", "role": "Capitaine"}],
            "Media": [{"id": rk * 10, "videoResolution": "1080", "videoCodec": "h264", "audioCodec": "aac",
                       "container": "mp4", "bitrate": 8000,
                       "Part": [{"id": rk * 100, "key": f"/library/parts/{rk*100}/1/file.mp4",
                                 "Stream": [{"id": rk * 1000 + 1, "streamType": 1, "codec": "h264"},
                                            {"id": rk * 1000 + 2, "streamType": 2, "codec": "aac",
                                             "languageCode": "fra", "displayTitle": "Français",
                                             "selected": True}]}]}],
        })
    show = {"ratingKey": "201", "key": "/library/metadata/201/children", "type": "show", "title": "Les Veilleurs",
            "year": 2021, "thumb": "/library/metadata/201/thumb/1700000000",
            "art": "/library/metadata/201/art/1700000000", "summary": "Une série de démonstration.",
            "leafCount": 5, "childCount": 2, "librarySectionID": 2, "addedAt": 1_700_100_000,
            "Genre": [{"tag": "Thriller"}]}
    anime = {"ratingKey": "301", "key": "/library/metadata/301/children", "type": "show", "title": "Kaze no Michi",
             "year": 2020, "thumb": "/library/metadata/301/thumb/1700000000",
             "art": "/library/metadata/301/art/1700000000", "summary": "Un anime de démonstration.",
             "leafCount": 2, "childCount": 1, "librarySectionID": 3, "addedAt": 1_700_200_000,
             "Genre": [{"tag": "Animation"}]}
    seasons = {
        "211": {"ratingKey": "211", "type": "season", "title": "Saison 1", "index": 1, "parentRatingKey": "201",
                "parentTitle": show["title"], "leafCount": 3, "thumb": "/library/metadata/211/thumb/1",
                "librarySectionID": 2},
        "212": {"ratingKey": "212", "type": "season", "title": "Saison 2", "index": 2, "parentRatingKey": "201",
                "parentTitle": show["title"], "leafCount": 2, "thumb": "/library/metadata/212/thumb/1",
                "librarySectionID": 2},
        "311": {"ratingKey": "311", "type": "season", "title": "Saison 1", "index": 1, "parentRatingKey": "301",
                "parentTitle": anime["title"], "leafCount": 2, "thumb": "/library/metadata/311/thumb/1",
                "librarySectionID": 3},
    }
    episodes = {
        "211": [ep(2111, show, 211, 1, 2, "Le Signal"), ep(2112, show, 211, 2, 2, "Les Ombres"),
                ep(2113, show, 211, 3, 2, "Point de Rupture")],
        "212": [ep(2121, show, 212, 1, 2, "Retour"), ep(2122, show, 212, 2, 2, "Final")],
        "311": [ep(3111 + i, anime, 311, i + 1, 3, t,
                   audio=[{"id": (3111 + i) * 1000 + 2, "streamType": 2, "codec": "aac", "languageCode": "jpn",
                           "displayTitle": "Japonais (AAC)", "selected": True},
                          {"id": (3111 + i) * 1000 + 3, "streamType": 2, "codec": "ac3", "languageCode": "fra",
                           "displayTitle": "Français (AC3 5.1)"}],
                   subs=[{"id": (3111 + i) * 1000 + 4, "streamType": 3, "codec": "srt", "languageCode": "fra",
                          "displayTitle": "Français (SRT Externe)", "key": f"/library/streams/{(3111+i)*1000+4}"},
                         {"id": (3111 + i) * 1000 + 5, "streamType": 3, "codec": "ass", "languageCode": "eng",
                          "displayTitle": "English (ASS)"}])
                for i, t in enumerate(["Le Départ", "La Montagne"])],
    }
    episodes["311"][0]["Marker"] = [{"type": "intro", "startTimeOffset": 30000, "endTimeOffset": 120000}]
    meta = {m["ratingKey"]: m for m in movies}
    meta.update({"201": show, "301": anime})
    meta.update(seasons)
    for lst in episodes.values():
        for e in lst:
            meta[e["ratingKey"]] = e
    return {"movies": movies, "show": show, "anime": anime, "seasons": seasons, "episodes": episodes,
            "meta": meta}


SRT = ("1\n00:00:01,000 --> 00:00:04,000\nBonjour <i>tout le monde</i>\n\n"
       "2\n00:00:05,500 --> 00:00:07,000\n{\\an8}Deuxième ligne\n").encode("cp1252")


def make_poster(path_key, w, h):
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return b"\xff\xd8\xff\xd9"
    seed = sum(map(ord, path_key))
    c1 = ((seed * 37) % 200 + 30, (seed * 91) % 160 + 40, (seed * 53) % 200 + 40)
    c2 = (c1[2] // 3, c1[0] // 3, c1[1] // 3)
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
    d.ellipse([w * 0.2, h * 0.15, w * 0.8, h * 0.15 + w * 0.6], outline=(255, 255, 255), width=max(2, w // 60))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


class FakePlex:
    def __init__(self):
        self.lib = build_library()
        self.requests = []
        self.puts = []
        self.stopped = []
        self.segment = None
        server = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                server.handle(self, "GET")

            def do_PUT(self):
                server.handle(self, "PUT")

        class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
        self.httpd = S(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()

    def ts_segment(self):
        if self.segment is None:
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "s.ts")
                try:
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                                    "testsrc2=s=640x360:r=25:d=6", "-f", "lavfi", "-i",
                                    "sine=frequency=440:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                    "-c:a", "aac", "-f", "mpegts", out], check=True, timeout=60)
                    with open(out, "rb") as f:
                        self.segment = f.read()
                except Exception:
                    self.segment = b"\x47" + b"\x00" * 187
        return self.segment

    def send(self, h, status, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def handle(self, h, method):
        u = urllib.parse.urlsplit(h.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path
        self.requests.append((method, path, q, dict(h.headers)))
        token = h.headers.get("X-Plex-Token") or q.get("X-Plex-Token")
        if token != TOKEN:
            return self.send(h, 401, {"error": "unauthorized"})
        lib = self.lib
        mc = lambda **kw: {"MediaContainer": kw}
        if method == "PUT" and path.startswith("/library/parts/"):
            self.puts.append((path, q))
            return self.send(h, 200, b"", "text/plain")
        if path == "/":
            return self.send(h, 200, mc(friendlyName="Plex de test", version="1.41.0"))
        if path == "/library/sections":
            return self.send(h, 200, mc(Directory=[
                {"key": "1", "title": "Films", "type": "movie"},
                {"key": "2", "title": "Séries", "type": "show"},
                {"key": "3", "title": "Animes", "type": "show"},
                {"key": "4", "title": "Musique", "type": "artist"}]))
        if path.startswith("/library/sections/") and path.endswith("/all"):
            sid = path.split("/")[3]
            start = int(q.get("X-Plex-Container-Start", 0))
            size = int(q.get("X-Plex-Container-Size", 50))
            if sid == "1":
                items = list(lib["movies"])
                if q.get("sort") == "addedAt:desc":
                    items.sort(key=lambda m: -m["addedAt"])
            elif q.get("type") == "4":
                items = [e for s in lib["seasons"].values() if str(s["librarySectionID"]) == sid
                         for e in lib["episodes"][s["ratingKey"]]]
            else:
                items = [{"2": lib["show"], "3": lib["anime"]}[sid]]
            if q.get("genre") and q["genre"] != "11":
                items = []
            items = [{k: v for k, v in m.items() if k != "librarySectionID"} for m in items]  # like Plex lists
            return self.send(h, 200, mc(totalSize=len(items), size=len(items[start:start + size]),
                                        librarySectionID=int(sid), Metadata=items[start:start + size]))
        if path.startswith("/library/sections/") and path.endswith("/genre"):
            return self.send(h, 200, mc(Directory=[{"key": "11", "title": "Drame"},
                                                   {"key": "/library/sections/1/all?genre=12", "title": "Aventure"}]))
        if path.startswith("/library/metadata/"):
            parts = path.split("/")
            rk = parts[3]
            m = lib["meta"].get(rk)
            if not m:
                return self.send(h, 404, b"", "text/plain")
            if len(parts) == 4:
                return self.send(h, 200, mc(Metadata=[m]))
            if parts[4] == "children":
                if m["type"] == "show":
                    kids = [s for s in lib["seasons"].values() if s["parentRatingKey"] == rk]
                else:
                    kids = lib["episodes"].get(rk, [])
                return self.send(h, 200, mc(librarySectionID=m["librarySectionID"], Metadata=kids))
            if parts[4] == "allLeaves":
                leaves = [e for s in sorted(lib["seasons"]) if lib["seasons"][s]["parentRatingKey"] == rk
                          for e in lib["episodes"][s]]
                return self.send(h, 200, mc(librarySectionID=m["librarySectionID"], Metadata=leaves))
        if path == "/hubs/search":
            qq = q.get("query", "").lower()
            hits = [m for m in lib["meta"].values() if qq in m["title"].lower() and m["type"] != "season"]
            return self.send(h, 200, mc(Hub=[{"type": "mixed", "Metadata": hits}]))
        if path == "/photo/:/transcode":
            return self.send(h, 200, make_poster(q.get("url", ""), int(q.get("width", 300)),
                                                 int(q.get("height", 450))), "image/jpeg")
        if path.startswith("/library/streams/"):
            return self.send(h, 200, SRT, "text/plain")
        if path == "/video/:/transcode/universal/start.m3u8":
            s = q.get("session")
            body = (f"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=640x360\n"
                    f"session/{s}/base/index.m3u8?X-Plex-Token={TOKEN}\n").encode()
            return self.send(h, 200, body, "application/vnd.apple.mpegurl")
        if path.startswith("/video/:/transcode/universal/session/") and path.endswith("index.m3u8"):
            body = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:0\n"
                    "#EXT-X-PLAYLIST-TYPE:VOD\n" + "".join(f"#EXTINF:6.0,\n{i:05d}.ts\n" for i in range(4))
                    + "#EXT-X-ENDLIST\n").encode()
            return self.send(h, 200, body, "application/vnd.apple.mpegurl")
        if path.startswith("/video/:/transcode/universal/session/") and path.endswith(".ts"):
            return self.send(h, 200, self.ts_segment(), "video/mp2t")
        if path == "/video/:/transcode/universal/stop":
            self.stopped.append(q.get("session"))
            return self.send(h, 200, b"", "text/plain")
        if path == "/video/:/transcode/universal/ping":
            return self.send(h, 200, b"", "text/plain")
        return self.send(h, 404, b"not found", "text/plain")
