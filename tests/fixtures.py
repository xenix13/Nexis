"""Génère une petite médiathèque de test avec ffmpeg, et un faux TMDB."""
import http.server
import io
import json
import os
import socketserver
import subprocess
import threading
import urllib.parse
import urllib.request

SRT_FR = "1\n00:00:01,000 --> 00:00:04,000\nBonjour <i>tout le monde</i>\n\n2\n00:00:05,500 --> 00:00:07,000\nDeuxième ligne\n"
SRT_EN = "1\n00:00:01,000 --> 00:00:03,000\nHello everyone\n"
ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 384
PlayResY: 288

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1
Style: Sign,DejaVu Sans,14,&H0000FFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,1,0,7,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,{\\an8}Le vent se lève\\Nsur la montagne
Dialogue: 1,0:00:01.00,0:00:12.00,Sign,,0,0,0,,{\\pos(20,200)\\p1}m 0 0 l 60 0 60 30 0 30{\\p0}
Dialogue: 2,0:00:01.00,0:00:12.00,Sign,,0,0,0,,{\\pos(30,240)}Guilde des aventuriers
"""
FONT_CANDIDATES = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


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
    # bonus locaux (conventions Plex) : dossier Trailers/ et suffixe -featurette
    os.makedirs(os.path.join(d, "Trailers"), exist_ok=True)
    ff(*src, "-t", "8", *x264, "-c:a", "aac", os.path.join(d, "Trailers", "Bande annonce officielle.mp4"))
    ff(*src, "-t", "8", *x264, "-c:a", "aac", os.path.join(d, "Le Grand Voyage (2019)-featurette.mkv"))

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
    # avec des sous-titres PGS (Blu-ray) : rectangle rouge de 1 à 4 s
    from tests.pgs import make_sup
    make_sup(os.path.join(tmp, "brume.sup"), size=(320, 180), box=(80, 20), pos=(120, 140))
    ff("-copyts", *src, "-i", os.path.join(tmp, "brume.sup"), "-t", "20", "-map", "0:v", "-map", "1:a", "-map", "2:s",
       "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=none",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "copy", "-metadata:s:s:0", "language=fre",
       os.path.join(d, "Brume.2022.HEVC.mkv"))
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
    font = next((f for f in FONT_CANDIDATES if os.path.isfile(f)), None)
    attach = ["-attach", font, "-metadata:s:t:0", "mimetype=application/x-truetype-font",
              "-metadata:s:t:0", "filename=KazeSign.ttf"] if font else []
    ff(*src, "-i", os.path.join(tmp, "a.ass"), "-i", os.path.join(tmp, "chapters.txt"), "-t", str(long_seconds),
       "-map", "0:v", "-map", "1:a", "-map", "2:s", "-map_chapters", "3", *attach,
       *x264, "-c:a", "aac", "-c:s", "ass", "-metadata:s:a:0", "language=jpn", "-metadata:s:s:0", "language=fre",
       os.path.join(d, "[Groupe] Kaze no Michi - 01 [1080p].mkv"))
    ff(*src, "-i", os.path.join(tmp, "fr.srt"), "-t", "15", "-map", "0:v", "-map", "1:a", "-map", "2:s", *x264,
       "-c:a", "aac", "-c:s", "srt", "-metadata:s:s:0", "language=fre",
       os.path.join(d, "[Groupe] Kaze no Michi - 02 [1080p].mkv"))
    # fichier non reconnu
    ff(*src, "-t", "3", *x264, "-c:a", "aac", os.path.join(d, "bonus making-of.mkv"))
    return {"films": films, "series": series, "animes": animes}


# ------------------------------------------------------------------ faux TMDB
SAGA = {"id": 9001, "name": "Saga Démo", "poster_path": "/c9001.jpg", "backdrop_path": "/cb9001.jpg"}
MOVIES = {
    5001: {"title": "Le Grand Voyage", "original_title": "Le Grand Voyage", "release_date": "2019-05-02",
           "overview": "Un voyage immense.", "genres": [{"name": "Aventure"}, {"name": "Drame"}],
           "belongs_to_collection": SAGA,
           "videos": {"results": [{"site": "YouTube", "key": "abcDEF12345", "type": "Trailer", "iso_639_1": "fr",
                                   "official": True},
                                  {"site": "YouTube", "key": "zzzzzzzzzzz", "type": "Teaser", "iso_639_1": "en"}]}},
    5002: {"title": "Nuit polaire", "original_title": "Polar Night", "release_date": "2021-11-10",
           "overview": "", "genres": [{"name": "Thriller"}]},
    5003: {"title": "Brume", "original_title": "Brume", "release_date": "2022-01-01",
           "overview": "La brume tombe.", "genres": [{"name": "Drame"}], "belongs_to_collection": SAGA},
}
SHOWS = {
    7001: {"name": "Les Veilleurs", "first_air_date": "2020-09-01", "overview": "Ils veillent.",
           "seasons": [{"season_number": 1, "episode_count": 3}, {"season_number": 2, "episode_count": 1}]},
    7002: {"name": "Kaze no Michi", "original_name": "風の道", "first_air_date": "2021-04-01", "overview": "Le vent.",
           "seasons": [{"season_number": 1, "episode_count": 12}]},
}


# catalogue « streaming » (titres absents de la bibliothèque, sauf 5001 et 7002)
STREAM = {
    "movie": [{"id": 9101, "title": "Film Netflix", "release_date": "2024-03-01", "poster_path": "/p9101.jpg",
               "overview": "Un film du catalogue.", "vote_average": 7.1, "popularity": 90},
              {"id": 5001, "title": "Le Grand Voyage", "release_date": "2019-05-02", "poster_path": "/p5001.jpg",
               "overview": "Un voyage immense.", "vote_average": 7.4, "popularity": 50}],
    "tv": [{"id": 9201, "name": "Série Netflix", "first_air_date": "2023-01-01", "poster_path": "/p9201.jpg",
            "overview": "Une série du catalogue.", "vote_average": 8.0, "popularity": 80},
           {"id": 7002, "name": "Kaze no Michi", "first_air_date": "2021-04-01", "poster_path": "/p7002.jpg",
            "overview": "Le vent.", "vote_average": 8.1, "popularity": 40}],
}
PROVIDERS = [{"provider_id": 8, "provider_name": "Netflix", "logo_path": "/logo8.jpg"},
             {"provider_id": 119, "provider_name": "Amazon Prime Video", "logo_path": "/logo119.jpg"},
             {"provider_id": 999, "provider_name": "Service obscur", "logo_path": "/logo999.jpg"}]


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
        if path.startswith("/watch/providers/"):
            return self.send(h, 200, {"results": PROVIDERS})
        if path.startswith("/discover/"):
            kind = path.rsplit("/", 1)[1]
            res = STREAM[kind] if q.get("with_watch_providers") in (None, "8") else []
            return self.send(h, 200, {"page": 1, "total_pages": 1, "results": res})
        if path == "/trending/all/week":
            return self.send(h, 200, {"results": [dict(r, media_type="movie") for r in STREAM["movie"]] +
                                                 [dict(r, media_type="tv") for r in STREAM["tv"]] +
                                                 [{"id": 1, "media_type": "person", "name": "Quelqu'un"}]})
        if path == "/search/multi":
            needle = q.get("query", "").lower()
            res = [dict(r, media_type=k) for k in ("movie", "tv") for r in STREAM[k]
                   if needle in (r.get("title") or r.get("name")).lower()]
            return self.send(h, 200, {"results": res})
        parts = path.strip("/").split("/")
        if parts[0] in ("movie", "tv") and int(parts[1]) in (9101, 9201):
            base = next(r for r in STREAM[parts[0]] if r["id"] == int(parts[1]))
            d = dict(base, genres=[{"name": "Drame"}], runtime=101, tagline="Accroche.",
                     backdrop_path=f"/b{parts[1]}.jpg",
                     credits={"cast": [{"name": "Acteur Deux"}]},
                     videos={"results": [{"site": "YouTube", "key": "trailer9999", "type": "Trailer",
                                          "iso_639_1": "fr"}]},
                     **{"watch/providers": {"results": {"FR": {"flatrate": [PROVIDERS[0]]}}}})
            if parts[0] == "tv":
                d["seasons"] = [{"season_number": 0, "name": "Spéciaux", "episode_count": 1},
                                {"season_number": 1, "name": "Saison 1", "episode_count": 8, "air_date": "2023-01-01"},
                                {"season_number": 2, "name": "Saison 2", "episode_count": 8, "air_date": "2024-01-01"}]
            return self.send(h, 200, d)
        if parts[0] == "collection" and int(parts[1]) == SAGA["id"]:
            return self.send(h, 200, {"id": SAGA["id"], "name": SAGA["name"], "overview": "Deux films de démonstration."})
        if parts[0] == "movie" and int(parts[1]) in MOVIES:
            m = dict(MOVIES[int(parts[1])])
            if lang == "en-US" and not m["overview"]:
                m["overview"] = "English overview."
            m.update(id=int(parts[1]), imdb_id=f"tt{int(parts[1]):07d}", vote_average=7.4, tagline="Une accroche.",
                     poster_path=f"/p{parts[1]}.jpg",
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
                     credits={"cast": []}, content_ratings={"results": [{"iso_3166_1": "FR", "rating": "12"}]},
                     external_ids={"imdb_id": f"tt{sid:07d}", "tvdb_id": 70000 + sid})
            return self.send(h, 200, m)
        return self.send(h, 404, {"status_message": "not found"})


# ------------------------------------------------------------------ faux Seerr
class FakeSeerr:
    """Imite l'API de Seerr (Overseerr / Jellyseerr)."""

    def __init__(self, key="cle-seerr-0123456789"):
        self.key = key
        self.users = [{"id": 1, "displayName": "colas", "email": "colas@example.org"},
                      {"id": 2, "displayName": "Léa M.", "username": "lea", "email": "lea@example.org"}]
        self.media = {("movie", 5003): 5}   # disponible côté Seerr (déjà téléchargé)
        self.requests = []
        server = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                server.handle(self, "GET")

            def do_POST(self):
                server.handle(self, "POST")

        class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
        self.httpd = S(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def send(self, h, code, body):
        data = json.dumps(body).encode()
        h.send_response(code)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def handle(self, h, method):
        u = urllib.parse.urlsplit(h.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        n = int(h.headers.get("Content-Length") or 0)
        body = json.loads(h.rfile.read(n)) if n else None
        if h.headers.get("X-Api-Key") != self.key:
            return self.send(h, 401, {"message": "Unauthorized"})
        acting = int(h.headers.get("X-API-User") or 1)
        path = u.path
        if path == "/api/v1/auth/me":
            return self.send(h, 200, {"id": 1, "displayName": "colas"})
        if path == "/api/v1/settings/public":
            return self.send(h, 200, {"applicationTitle": "Seerr", "mediaServerType": 2})
        if path == "/api/v1/media":
            res = [{"tmdbId": t, "mediaType": k, "status": st} for (k, t), st in self.media.items()]
            return self.send(h, 200, {"pageInfo": {"pages": 1, "results": len(res)}, "results": res})
        if path == "/api/v1/user" and method == "GET":
            return self.send(h, 200, {"pageInfo": {"pages": 1}, "results": self.users})
        if path == "/api/v1/request" and method == "POST":
            key = (body["mediaType"], body["mediaId"])
            if self.media.get(key) in (2, 3, 5):
                return self.send(h, 409, {"message": "Request for this media already exists."})
            self.requests.append({"user": acting, **body})
            self.media[key] = 2
            return self.send(h, 201, {"id": len(self.requests), "status": 1, "media": {"status": 2}})
        return self.send(h, 404, {"message": "not found"})
