"""Génère une petite médiathèque de test avec ffmpeg, et un faux TMDB."""
import http.server
import io
import json
import os
import socketserver
import subprocess
import threading
import urllib.parse

SRT_FR = "1\n00:00:01,000 --> 00:00:04,000\nBonjour <i>tout le monde</i>\n\n2\n00:00:05,500 --> 00:00:07,000\nDeuxième ligne\n"
SRT_EN = "1\n00:00:01,000 --> 00:00:03,000\nHello everyone\n"
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 384
PlayResY: 288

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,{\\an8}Le vent se lève\\Nsur la montagne
"""


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True, timeout=300)


def make_media(root, long_seconds=90):
    """Crée films/, series/, animes/. Retourne le dict des chemins."""
    films = os.path.join(root, "films")
    series = os.path.join(root, "series")
    animes = os.path.join(root, "animes")
    tmp = os.path.join(root, "_tmp")
    for d in (films, series, animes, tmp):
        os.makedirs(d, exist_ok=True)
    src = ["-f", "lavfi", "-i", "testsrc2=s=320x180:r=24", "-f", "lavfi", "-i", "sine=frequency=440"]
    x264 = ["-c:v", "libx264", "-preset", "ultrafast", "-g", "48", "-pix_fmt", "yuv420p"]
    with open(os.path.join(tmp, "fr.srt"), "w") as f:
        f.write(SRT_FR)
    with open(os.path.join(tmp, "a.ass"), "w") as f:
        f.write(ASS)

    # 1. MP4 H.264 + AAC dans son dossier, avec poster.jpg -> lecture directe
    d = os.path.join(films, "Le Grand Voyage (2019)")
    os.makedirs(d, exist_ok=True)
    ff(*src, "-t", str(long_seconds), *x264, "-c:a", "aac", "-movflags", "+faststart",
       os.path.join(d, "Le Grand Voyage (2019).mp4"))
    ff("-f", "lavfi", "-i", "color=c=0x335577:s=400x600", "-frames:v", "1", os.path.join(d, "poster.jpg"))

    # 2. MKV H.264, deux pistes audio (AAC fra + AC3 jpn), SRT interne, SRT externe -> remux
    ff(*src, "-f", "lavfi", "-i", "sine=frequency=880", "-i", os.path.join(tmp, "fr.srt"),
       "-t", str(long_seconds), "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", *x264,
       "-c:a:0", "aac", "-c:a:1", "ac3", "-c:s", "srt",
       "-metadata:s:a:0", "language=fre", "-metadata:s:a:1", "language=jpn", "-metadata:s:s:0", "language=fre",
       os.path.join(films, "Nuit.Polaire.2021.1080p.BluRay.x264.mkv"))
    with open(os.path.join(films, "Nuit.Polaire.2021.1080p.BluRay.x264.en.srt"), "w") as f:
        f.write(SRT_EN)

    # 3. HEVC -> transcodage
    d = os.path.join(films, "Brume (2022)")
    os.makedirs(d, exist_ok=True)
    ff(*src, "-t", "20", "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=none",
       "-pix_fmt", "yuv420p", "-c:a", "aac", os.path.join(d, "Brume.2022.HEVC.mkv"))
    # un .nfo avec un identifiant TMDB
    with open(os.path.join(d, "Brume.2022.HEVC.nfo"), "w") as f:
        f.write("<movie><title>Brume</title><year>2022</year><uniqueid type=\"tmdb\">5003</uniqueid></movie>")

    # 4. Série classique
    for s, e in ((1, 1), (1, 2), (1, 3), (2, 1)):
        d = os.path.join(series, "Les Veilleurs", f"Season {s:02d}")
        os.makedirs(d, exist_ok=True)
        ff(*src, "-t", "12", *x264, "-c:a", "aac", os.path.join(d, f"Les.Veilleurs.S{s:02d}E{e:02d}.720p.WEB.mkv"))

    # 5. Anime : numérotation « - 01 », sous-titres ASS ou SRT internes
    # (ffmpeg ne sait pas fabriquer de sous-titres image PGS/VobSub : l'incrustation est testée à part)
    d = os.path.join(animes, "Kaze no Michi")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(tmp, "chapters.txt"), "w") as f:
        f.write(";FFMETADATA1\n")
        for start, end, title in ((0, 10, "Prologue"), (10, 40, "Opening"), (40, 70, "Partie A"), (70, 85, "Ending"),
                                  (85, long_seconds, "Preview")):
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start * 1000}\nEND={end * 1000}\ntitle={title}\n")
    ff(*src, "-i", os.path.join(tmp, "a.ass"), "-i", os.path.join(tmp, "chapters.txt"), "-t", str(long_seconds),
       "-map", "0:v", "-map", "1:a", "-map", "2:s", "-map_chapters", "3",
       *x264, "-c:a", "aac", "-c:s", "ass", "-metadata:s:a:0", "language=jpn", "-metadata:s:s:0", "language=fre",
       os.path.join(d, "[Groupe] Kaze no Michi - 01 [1080p].mkv"))
    ff(*src, "-i", os.path.join(tmp, "fr.srt"), "-t", "15", "-map", "0:v", "-map", "1:a", "-map", "2:s", *x264,
       "-c:a", "aac", "-c:s", "srt", "-metadata:s:s:0", "language=fre",
       os.path.join(d, "[Groupe] Kaze no Michi - 02 [1080p].mkv"))
    # fichier non reconnu
    ff(*src, "-t", "3", *x264, "-c:a", "aac", os.path.join(d, "bonus making-of.mkv"))
    return {"films": films, "series": series, "animes": animes}


# ------------------------------------------------------------------ faux TMDB
MOVIES = {
    5001: {"title": "Le Grand Voyage", "original_title": "Le Grand Voyage", "release_date": "2019-05-02",
           "overview": "Un voyage immense.", "genres": [{"name": "Aventure"}, {"name": "Drame"}]},
    5002: {"title": "Nuit polaire", "original_title": "Polar Night", "release_date": "2021-11-10",
           "overview": "", "genres": [{"name": "Thriller"}]},
    5003: {"title": "Brume", "original_title": "Brume", "release_date": "2022-01-01",
           "overview": "La brume tombe.", "genres": [{"name": "Drame"}]},
}
SHOWS = {
    7001: {"name": "Les Veilleurs", "first_air_date": "2020-09-01", "overview": "Ils veillent.",
           "seasons": [{"season_number": 1, "episode_count": 3}, {"season_number": 2, "episode_count": 1}]},
    7002: {"name": "Kaze no Michi", "original_name": "風の道", "first_air_date": "2021-04-01", "overview": "Le vent.",
           "seasons": [{"season_number": 1, "episode_count": 12}]},
}


class FakeTMDB:
    def __init__(self, key="cle-de-test"):
        self.key = key
        self.calls = []
        server = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                server.handle(self)

        class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
        self.httpd = S(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()

    def send(self, h, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        h.send_response(code)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def handle(self, h):
        u = urllib.parse.urlsplit(h.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path
        self.calls.append((path, q))
        if path.startswith("/img/"):
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGB", (300, 450), (180, 90, 60)).save(buf, "JPEG")
            return self.send(h, 200, buf.getvalue(), "image/jpeg")
        if q.get("api_key") != self.key:
            return self.send(h, 401, {"status_message": "Invalid API key"})
        lang = q.get("language", "fr-FR")
        if path == "/configuration":
            return self.send(h, 200, {"images": {}})
        if path in ("/search/movie", "/search/tv"):
            db = MOVIES if path.endswith("movie") else SHOWS
            needle = q.get("query", "").lower()
            res = []
            for i, m in db.items():
                name = (m.get("title") or m.get("name")).lower()
                if needle and (needle in name or name in needle):
                    res.append({"id": i, "title": m.get("title"), "name": m.get("name"),
                                "original_title": m.get("original_title"), "release_date": m.get("release_date"),
                                "first_air_date": m.get("first_air_date"), "overview": m["overview"],
                                "poster_path": f"/p{i}.jpg"})
            return self.send(h, 200, {"results": res})
        parts = path.strip("/").split("/")
        if parts[0] == "movie" and int(parts[1]) in MOVIES:
            m = dict(MOVIES[int(parts[1])])
            if lang == "en-US" and not m["overview"]:
                m["overview"] = "English overview."
            m.update(id=int(parts[1]), vote_average=7.4, tagline="Une accroche.", poster_path=f"/p{parts[1]}.jpg",
                     backdrop_path=f"/b{parts[1]}.jpg", production_companies=[{"name": "Studio Démo"}],
                     credits={"cast": [{"name": "Actrice Une", "character": "Héroïne"}],
                              "crew": [{"job": "Director", "name": "Réalisatrice"}]},
                     release_dates={"results": [{"iso_3166_1": "FR", "release_dates": [{"certification": "U"}]}]})
            return self.send(h, 200, m)
        if parts[0] == "tv" and int(parts[1]) in SHOWS:
            sid = int(parts[1])
            if len(parts) == 4 and parts[2] == "season":
                n = int(parts[3])
                eps = [{"episode_number": e, "name": f"Titre {n}x{e}", "overview": f"Résumé {n}x{e}.",
                        "still_path": f"/s{sid}_{n}_{e}.jpg", "air_date": "2020-09-0%d" % min(e, 9)}
                       for e in range(1, 13)]
                return self.send(h, 200, {"episodes": eps})
            m = dict(SHOWS[sid])
            m.update(id=sid, vote_average=8.1, poster_path=f"/p{sid}.jpg", backdrop_path=f"/b{sid}.jpg",
                     genres=[{"name": "Animation" if sid == 7002 else "Thriller"}], networks=[{"name": "Canal"}],
                     credits={"cast": []}, content_ratings={"results": [{"iso_3166_1": "FR", "rating": "12"}]})
            return self.send(h, 200, m)
        return self.send(h, 404, {"status_message": "not found"})
