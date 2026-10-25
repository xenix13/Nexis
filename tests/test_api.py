"""Tests de bout en bout : vrais fichiers vidéo (ffmpeg), faux TMDB.

python3 -m unittest discover -s tests -t .
"""
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.fixtures import FakeSeerr, FakeTMDB, make_media  # noqa: E402

TMDB = FakeTMDB()
os.environ["SEANCE_TMDB_API"] = TMDB.url
os.environ["SEANCE_TMDB_IMG"] = TMDB.url + "/img"

from seance.api import Ctx, build_app  # noqa: E402
from seance.subtitles import parse_subtitles  # noqa: E402
from seance.transcoder import plan  # noqa: E402
from seance.web import Server, make_handler  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
MEDIA_CACHE = os.path.join(tempfile.gettempdir(), "seance-test-media-v6")


def media_root():
    if not os.path.isfile(os.path.join(MEDIA_CACHE, "ok")):
        shutil.rmtree(MEDIA_CACHE, ignore_errors=True)
        make_media(MEDIA_CACHE)
        open(os.path.join(MEDIA_CACHE, "ok"), "w").close()
    return MEDIA_CACHE


class Client:
    def __init__(self, base):
        self.base = base
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                                  urllib.request.ProxyHandler({}))

    def call(self, method, path, body=None, headers=None, csrf=True, raw=False):
        h = {"Content-Type": "application/json"}
        if csrf:
            h["X-Seance"] = "1"
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None)
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with self.opener.open(req, timeout=120) as r:
                content, status, hdrs = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            content, status, hdrs = e.read(), e.code, dict(e.headers)
        if raw:
            return status, content, hdrs
        try:
            return status, json.loads(content)
        except json.JSONDecodeError:
            return status, content


def hls_segment(client, url, n=None, at=None):
    """Playlist VOD + init fMP4 + segment n (ou celui qui contient `at` secondes) -> (n, octets lisibles)."""
    st, pl, _ = client.call("GET", url, raw=True)
    assert st == 200, pl
    durs = [float(l[8:].split(",")[0]) for l in pl.decode().splitlines() if l.startswith("#EXTINF:")]
    if n is None:
        t, n = 0.0, 0
        for i, d in enumerate(durs):
            if t <= at < t + d:
                n = i
                break
            t += d
    base = url.rsplit("/", 1)[0]
    st, init, _ = client.call("GET", base + "/init.mp4", raw=True)
    assert st == 200, init
    st, seg, _ = client.call("GET", f"{base}/{n}.m4s", raw=True)
    assert st == 200, seg
    return n, init + seg, durs


def first_pts(data, kind):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", kind, "-show_entries", "packet=pts_time",
                          "-of", "csv=p=0", "-read_intervals", "%+#1", "-i", "-"], input=data,
                         capture_output=True, timeout=30).stdout.decode().split()
    return float(out[0].strip(","))


def wait_scan(client, timeout=180):
    deadline = time.time() + timeout
    time.sleep(0.3)
    while time.time() < deadline:
        st, s = client.call("GET", "/api/admin/scan")
        if not s["running"]:
            return s
        time.sleep(0.3)
    raise AssertionError("scan trop long")


def ffprobe_codec(data):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                        "-of", "csv=p=0", "-i", "-"], input=data, capture_output=True, timeout=30)
    return p.stdout.decode().split()[0] if p.stdout.strip() else ""


class SeanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = media_root()
        cls.work = tempfile.mkdtemp()
        cls.media = os.path.join(cls.work, "media")
        shutil.copytree(root, cls.media)
        from seance import scanner
        scanner.RECENT_SECONDS = 0  # les fichiers de test viennent d'être créés
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.server = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "colas", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        st, r = cls.admin.call("PUT", "/api/admin/settings", {"tmdb_key": TMDB.key})
        assert st == 200, r
        cls.libs = {}
        for name, kind, sub in (("Films", "movie", "films"), ("Séries", "show", "series"), ("Animes", "show", "animes")):
            wait_scan(cls.admin)
            st, lib = cls.admin.call("POST", "/api/admin/libraries",
                                     {"name": name, "kind": kind, "paths": [os.path.join(cls.media, sub)]})
            assert st == 200, lib
            cls.libs[name] = lib["id"]
        cls.scan = wait_scan(cls.admin)
        st, u = cls.admin.call("POST", "/api/admin/users", {
            "username": "lea", "password": "motdepasse2", "display_name": "Léa",
            "all_libraries": False, "libraries": [cls.libs["Films"], cls.libs["Animes"]]})
        assert st == 200, u
        cls.lea = Client(cls.base)
        assert cls.lea.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})[0] == 200

    @classmethod
    def tearDownClass(cls):
        cls.ctx.transcoder.stop_all()
        cls.server.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    # helpers
    def items(self, lib, client=None):
        st, r = (client or self.admin).call("GET", f"/api/libraries/{self.libs[lib]}/items?size=100")
        self.assertEqual(st, 200, r)
        return {c["title"]: c for c in r["items"]}

    def movie(self, title):
        return self.items("Films")[title]

    def episodes(self, show_title, lib):
        show = self.items(lib)[show_title]
        st, d = self.admin.call("GET", f"/api/items/{show['id']}")
        out = []
        for s in d["seasons"]:
            st, eps = self.admin.call("GET", f"/api/items/{s['id']}/children")
            out += eps
        return show, d, out

    def test_admin_system_load(self):
        st, r = self.admin.call("GET", "/api/admin/system")
        self.assertEqual(st, 200, r)
        self.assertGreater(r["mem_total"], 0)
        self.assertIn("swap_used", r)
        self.assertGreaterEqual(r["ffmpeg"], 0)
        self.assertEqual(self.lea.call("GET", "/api/admin/system")[0], 403)

    def test_accented_usernames(self):
        import unicodedata
        st, u = self.admin.call("POST", "/api/admin/users", {"username": "Héloïse", "password": "M0t$de@passe!?%"})
        self.assertEqual(st, 200, u)
        # même compte quelle que soit la casse ou la forme des accents (é en un ou deux caractères)
        self.assertEqual(self.admin.call("POST", "/api/admin/users", {"username": "HÉLOÏSE", "password": "motdepasse9"})[0], 409)
        for name in ("Héloïse", "héloïse", "HÉLOÏSE", unicodedata.normalize("NFD", "Héloïse")):
            c = Client(self.base)
            st, r = c.call("POST", "/api/login", {"username": name, "password": "M0t$de@passe!?%"})
            self.assertEqual(st, 200, (name, r))
        self.assertEqual(self.admin.call("POST", "/api/admin/users", {"username": "a b", "password": "motdepasse9"})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/admin/users", {"username": "x@y", "password": "motdepasse9"})[0], 400)
        self.admin.call("DELETE", f"/api/admin/users/{u['id']}")

    # ------------------------------------------------------------- scan / meta
    def test_scan_counts_and_unrecognized(self):
        st, r = self.admin.call("GET", "/api/admin/libraries")
        by = {l["name"]: l for l in r["libraries"]}
        self.assertEqual(by["Films"]["counts"].get("movie"), 3)
        self.assertEqual(by["Séries"]["counts"], {"show": 1, "season": 2, "episode": 4})
        self.assertEqual(by["Animes"]["counts"], {"show": 1, "season": 1, "episode": 2})
        st, s = self.admin.call("GET", "/api/admin/scan")
        self.assertFalse(s["errors"], s["errors"])

    def test_tmdb_metadata(self):
        m = self.items("Films")
        self.assertEqual(set(m), {"Le Grand Voyage", "Nuit polaire", "Brume"})
        st, d = self.admin.call("GET", f"/api/items/{m['Le Grand Voyage']['id']}")
        self.assertEqual(d["genres"], ["Aventure", "Drame"])
        self.assertEqual(d["tmdb_id"], 5001)
        self.assertEqual(d["cast"][0]["name"], "Actrice Une")
        self.assertEqual(d["content_rating"], "U")
        st, d = self.admin.call("GET", f"/api/items/{m['Nuit polaire']['id']}")
        self.assertEqual(d["summary"], "English overview.")  # repli en anglais
        self.assertEqual(d["year"], 2021)
        st, d = self.admin.call("GET", f"/api/items/{m['Brume']['id']}")
        self.assertEqual(d["tmdb_id"], 5003)  # depuis le .nfo
        show, d, eps = self.episodes("Les Veilleurs", "Séries")
        self.assertEqual([(e["season"], e["episode"], e["title"]) for e in eps],
                         [(1, 1, "Titre 1x1"), (1, 2, "Titre 1x2"), (1, 3, "Titre 1x3"), (2, 1, "Titre 2x1")])
        show, d, eps = self.episodes("Kaze no Michi", "Animes")
        self.assertEqual(d["genres"], ["Animation"])
        self.assertEqual([e["title"] for e in eps], ["Titre 1x1", "Titre 1x2"])

    def test_images(self):
        m = self.movie("Le Grand Voyage")
        row = self.ctx.db.one("SELECT poster, backdrop FROM items WHERE id=?", (int(m["id"]),))
        self.assertTrue(row["poster"].startswith("local:"))  # poster.jpg prioritaire
        self.assertEqual(row["backdrop"], "tmdb:/b5001.jpg")
        for path in (m["thumb"], m["art"]):
            st, body, h = self.admin.call("GET", f"/api/img?p={path}&w=200&h=300", raw=True)
            self.assertEqual(st, 200, path)
            self.assertEqual(body[:2], b"\xff\xd8")
        show, d, eps = self.episodes("Les Veilleurs", "Séries")
        st, body, h = self.admin.call("GET", f"/api/img?p={eps[0]['thumb']}&w=320&h=180", raw=True)
        self.assertEqual(st, 200)
        for bad in ("/etc/passwd", "/library/metadata/1/../../x", "/library/metadata/1/poster/1"):
            self.assertEqual(self.admin.call("GET", "/api/img?p=" + bad, raw=True)[0], 400, bad)

    def test_search_folds_accents(self):
        st, r = self.lea.call("GET", "/api/search?q=NUIT")
        self.assertEqual([c["title"] for c in r["movies"]], ["Nuit polaire"])
        st, r = self.lea.call("GET", "/api/search?q=veilleurs")
        self.assertEqual(r["shows"], [])  # Léa n'a pas accès aux Séries
        st, r = self.admin.call("GET", "/api/search?q=veilleurs")
        self.assertEqual(len(r["shows"]), 1)
        st, r = self.admin.call("GET", "/api/search?q=100%")
        self.assertEqual(st, 200)

    def test_genre_filter(self):
        st, g = self.admin.call("GET", f"/api/libraries/{self.libs['Films']}/genres")
        self.assertIn("Drame", [x["id"] for x in g])
        st, r = self.admin.call("GET", f"/api/libraries/{self.libs['Films']}/items?genre=Thriller")
        self.assertEqual([c["title"] for c in r["items"]], ["Nuit polaire"])

    # --------------------------------------------------------------- lecture
    def test_plan_decisions(self):
        p = {"container": "matroska,webm", "bitrate": 3_000_000,
             "video": {"index": 0, "codec": "h264", "profile": "High 10", "pix_fmt": "yuv420p10le", "height": 1080},
             "audio": [{"index": 1, "codec": "flac", "channels": 2}]}
        r = plan(p)
        self.assertEqual(r["mode"], "transcode")
        self.assertIn("10 bits", " ".join(r["reasons"]))
        p["video"].update(profile="High", pix_fmt="yuv420p")
        self.assertEqual(plan(p)["mode"], "copy")
        self.assertEqual(plan(p, quality="720p")["mode"], "transcode")
        # sous-titres image (PGS) : incrustation par overlay, donc transcodage
        from seance.transcoder import build_command
        p["subs"] = [{"index": 2, "codec": "hdmv_pgs_subtitle"}]
        p["video"]["width"] = 1920
        r = plan(p, burn_index=2)
        self.assertEqual(r["mode"], "transcode")
        self.assertIn("sous-titres incrustés", r["reasons"])
        cmd = build_command("/x.mkv", r, 12.5, "/tmp/out", 2)
        self.assertIn("[0:2]format=yuva420p[s];[0:0][s]overlay=eof_action=pass:repeatlast=0[o];[o]format=yuv420p[v]", cmd)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "12.500")
        self.assertEqual(cmd[cmd.index("-canvas_size") + 1], "1920x1080")  # « unspecified size » évité
        self.assertEqual(cmd[cmd.index("-start_number") + 1], "2")
        self.assertIn("-copyts", cmd)
        # remux : on vise juste après l'image clé
        cmd = build_command("/x.mkv", plan(dict(p, video=dict(p["video"], profile="High", pix_fmt="yuv420p"))), 12.0,
                            "/tmp/out")
        self.assertEqual(cmd[cmd.index("-ss") + 1], "12.250")
        # profil du navigateur : HEVC lu tel quel s'il le sait, sinon converti ; 10 bits à part
        hevc = {"container": "matroska,webm", "bitrate": 0, "video": {"index": 0, "codec": "hevc", "profile": "Main 10",
                "pix_fmt": "yuv420p10le", "height": 1080}, "audio": [{"index": 1, "codec": "eac3", "channels": 6,
                                                                       "layout": "5.1(side)"}]}
        caps = {"video": {"h264", "hevc"}, "audio": {"aac", "eac3"}, "containers": {"mp4"}}
        self.assertEqual(plan(hevc, caps=caps)["mode"], "transcode")
        caps["video"].add("hevc10")
        r = plan(hevc, caps=caps, algo="dave750", boost=1.5)
        self.assertEqual((r["mode"], r["acopy"]), ("copy", False))  # 5.1 : mixé en stéréo par Nexis
        self.assertTrue(r["audio_filter"].startswith("pan=stereo|c0=0.5*c2+0.707*c0"), r["audio_filter"])
        self.assertIn("volume=1.5", r["audio_filter"])
        self.assertIn("hvc1", build_command("/x.mkv", r, 0, "/tmp/out"))
        # un MP4 H.264/AAC est lu directement, sauf si on choisit la 2e piste audio
        mp4 = {"container": "mov,mp4,m4a,3gp,3g2,mj2", "bitrate": 0,
               "video": {"index": 0, "codec": "h264", "profile": "High", "pix_fmt": "yuv420p", "height": 720},
               "audio": [{"index": 1, "codec": "aac", "channels": 2}, {"index": 2, "codec": "aac", "channels": 2}]}
        self.assertEqual(plan(mp4)["mode"], "direct")
        self.assertEqual(plan(mp4, audio_index=2)["mode"], "copy")

    def test_hardware_transcoding(self):
        """Commandes NVIDIA / VAAPI, refus d'un moteur absent, repli automatique sur le processeur."""
        from seance.transcoder import build_command, plan
        p = {"container": "matroska,webm", "bitrate": 3_000_000, "subs": [{"index": 2, "codec": "hdmv_pgs_subtitle"}],
             "video": {"index": 0, "codec": "hevc", "profile": "Main 10", "pix_fmt": "yuv420p10le", "height": 1080,
                       "width": 1920}, "audio": [{"index": 1, "codec": "aac", "channels": 2}]}
        r = plan(p, quality="720p")
        nv = build_command("/x.mkv", r, 0, "/tmp/out", hw={"engine": "nvenc", "device": ""})
        self.assertLess(nv.index("-hwaccel"), nv.index("-i"))  # décodage par la carte : option d'entrée
        self.assertEqual(nv[nv.index("-codec:v:0") + 1], "h264_nvenc")
        self.assertIn("scale=-2:720:flags=bicubic,format=yuv420p", nv)
        va = build_command("/x.mkv", plan(p, burn_index=2), 0, "/tmp/out", hw={"engine": "vaapi", "device": "/dev/dri/renderD128"})
        self.assertIn("vaapi=va:/dev/dri/renderD128", va)
        self.assertEqual(va[va.index("-codec:v:0") + 1], "h264_vaapi")
        self.assertTrue(va[va.index("-filter_complex") + 1].endswith("[o]format=nv12,hwupload[v]"))  # incrustation puis envoi à la carte
        cp = build_command("/x.mkv", plan(dict(p, video=dict(p["video"], codec="h264", profile="High", pix_fmt="yuv420p"))),
                           0, "/tmp/out", hw={"engine": "nvenc", "device": ""})
        self.assertNotIn("-hwaccel", cp)  # remux : la carte ne sert à rien
        # réglage : on n'enregistre qu'un moteur qui passe un vrai essai (pas de carte sur la machine de test)
        st, r = self.admin.call("PUT", "/api/admin/settings", {"hw_engine": "nvenc"})
        self.assertEqual(st, 400)
        self.assertIn("Test du transcodage matériel en échec", r["error"])
        self.assertEqual(self.admin.call("PUT", "/api/admin/settings", {"hw_engine": "quantum"})[0], 400)
        self.assertEqual(self.admin.call("PUT", "/api/admin/settings", {"hw_engine": "vaapi", "hw_device": "/etc/passwd"})[0], 400)
        st, r = self.admin.call("GET", "/api/admin/settings")
        self.assertEqual(r["hw_engine"], "none")
        # carte qui refuse en pleine lecture : on repart sur le processeur, la lecture continue
        self.ctx.db.set_setting("hw_engine", "vaapi")
        self.ctx.db.set_setting("hw_device", "/dev/dri/renderD199")
        try:
            row = self.ctx.db.one("SELECT key, probe, duration_ms FROM items WHERE title='Le Grand Voyage'")
            tp = dict(plan(json.loads(row["probe"])), mode="transcode")  # fichier de test trop petit pour l'imposer
            t = self.ctx.transcoder
            t.start("hwtest", row["key"], tp, 0, row["duration_ms"])
            self.assertEqual(t.get("hwtest").hw["engine"], "vaapi")
            seg = t.segment("hwtest", "0.m4s")
            with open(seg, "rb") as f:
                self.assertGreater(len(f.read()), 1000)
            self.assertIsNone(t.stats("hwtest")["hw"])  # repassé sur le processeur
            t.stop("hwtest")
        finally:
            self.ctx.db.set_setting("hw_engine", "none")

    def test_direct_play_with_range(self):
        m = self.movie("Le Grand Voyage")
        st, p = self.admin.call("POST", "/api/play", {"id": m["id"], "offset": 30000})
        self.assertEqual(st, 200, p)
        self.assertEqual(p["mode"], "direct")
        self.assertEqual(p["offset"], 30000)
        st, body, h = self.admin.call("GET", p["url"], headers={"Range": "bytes=0-99"}, raw=True)
        self.assertEqual(st, 206)
        self.assertEqual(len(body), 100)
        self.assertTrue(h["Content-Range"].startswith("bytes 0-99/"))
        self.assertEqual(body[4:8], b"ftyp")
        self.assertEqual(self.lea.call("GET", p["url"], raw=True)[0], 404)  # flux d'un autre utilisateur
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})

    def test_remux_hls_with_seek(self):
        m = self.movie("Nuit polaire")
        st, d = self.admin.call("GET", f"/api/items/{m['id']}")
        self.assertEqual([a["lang"] for a in d["audio"]], ["fra", "jpn"])
        self.assertEqual([(s["lang"], s["external"]) for s in d["subtitles"]], [("fra", False), ("eng", True)])
        jpn = d["audio"][1]["id"]
        st, p = self.admin.call("POST", "/api/play", {"id": m["id"], "offset": 41000, "audio": jpn})
        self.assertEqual(st, 200, p)
        self.assertEqual(p["mode"], "copy")
        self.assertIn("audio AC3 converti", p["reasons"])
        self.assertEqual(p["offset"], 41000)  # chronologie complète : la position est absolue
        self.assertTrue(p["url"].endswith("/main.m3u8"))
        n, data, durs = hls_segment(self.admin, p["url"], at=41.0)
        self.assertEqual(ffprobe_codec(data), "h264")
        # segments calés sur les images clés du MKV (GOP de 2 s -> 6 s), horodatages d'origine
        self.assertTrue(all(abs(d - 6.0) < 0.01 for d in durs[:-1]), durs)
        start = sum(durs[:n])
        v, a = first_pts(data, "v"), first_pts(data, "a")
        self.assertLess(abs(v - start), 0.1, (v, start))
        self.assertLess(abs(a - v), 0.35, (a, v))  # le son suit tout de suite
        # saut loin devant : le serveur relance ffmpeg au bon segment (comme Jellyfin)
        last = len(durs) - 2
        n2, data2, _ = hls_segment(self.admin, p["url"], n=last)
        self.assertLess(abs(first_pts(data2, "v") - sum(durs[:last])), 0.1)
        self.assertEqual(self.ctx.transcoder.get(p["stream"]).run_start, last)
        # saut : l'ancien ffmpeg est arrêté
        st, p2 = self.admin.call("POST", "/api/play", {"id": m["id"], "offset": 70000, "replace": p["stream"]})
        self.assertEqual(st, 200, p2)
        self.assertIsNone(self.ctx.transcoder.get(p["stream"]))
        self.assertEqual(self.admin.call("GET", p["url"], raw=True)[0], 404)
        self.admin.call("POST", "/api/stop", {"stream": p2["stream"]})
        self.assertIsNone(self.ctx.transcoder.get(p2["stream"]))

    def test_transcode_hevc_and_limit(self):
        m = self.movie("Brume")
        self.admin.call("PUT", "/api/admin/settings", {"max_transcodes": 1})
        st, p = self.admin.call("POST", "/api/play", {"id": m["id"]})
        self.assertEqual(st, 200, p)
        self.assertEqual(p["mode"], "transcode")
        self.assertIn("vidéo HEVC", " ".join(p["reasons"]))
        st, p2 = self.admin.call("POST", "/api/play", {"id": m["id"]})
        self.assertEqual(st, 503)
        self.assertIn("transcodages simultanés", p2["error"])
        n, data, durs = hls_segment(self.admin, p["url"], n=0)
        self.assertEqual(ffprobe_codec(data), "h264")
        self.assertAlmostEqual(sum(durs), 20.0, delta=0.5)
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})
        self.admin.call("PUT", "/api/admin/settings", {"max_transcodes": 2})

    def test_pgs_rendered_by_browser(self):
        m = self.movie("Brume")
        st, d = self.admin.call("GET", f"/api/items/{m['id']}")
        pgs = d["subtitles"][0]
        self.assertTrue(pgs["pgs"])
        self.assertFalse(pgs["overlay"])
        # navigateur capable : pas d'incrustation, le .sup est envoyé tel quel
        caps = {"video": ["h264"], "audio": ["aac"], "containers": ["mp4"], "pgs": True}
        for _ in range(20):
            st, r = self.admin.call("GET", f"/api/items/{m['id']}/prepare?wait=5")
            if r["ready"]:
                break
        st, p = self.admin.call("POST", "/api/play", {"id": m["id"], "subtitle": pgs["id"], "caps": caps})
        self.assertEqual(st, 200, p)
        self.assertNotIn("sous-titres incrustés", p["reasons"])
        self.assertEqual(p["subtitle_pgs"], f"/api/items/{m['id']}/subtitles/{pgs['id']}/sup")
        st, sup, _ = self.admin.call("GET", p["subtitle_pgs"], raw=True)
        self.assertEqual((st, sup[:2]), (200, b"PG"))
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})
        # navigateur sans rendu PGS : incrustation (avec la taille du canevas, sinon « unspecified size »)
        st, p = self.admin.call("POST", "/api/play", {"id": m["id"], "subtitle": pgs["id"], "offset": 2000})
        self.assertIn("sous-titres incrustés", p["reasons"])
        self.assertIsNone(p["subtitle_pgs"])
        n, data, durs = hls_segment(self.admin, p["url"], n=0)
        self.assertEqual(ffprobe_codec(data), "h264")
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})

    def test_subtitles_overlay_and_burn(self):
        show, d, eps = self.episodes("Kaze no Michi", "Animes")
        st, e1 = self.admin.call("GET", f"/api/items/{eps[0]['id']}")
        ass = e1["subtitles"][0]
        self.assertTrue(ass["overlay"])
        st, p = self.admin.call("POST", "/api/play", {"id": e1["id"], "subtitle": ass["id"]})
        self.assertEqual(p["mode"], "copy")  # l'ASS est affiché par le navigateur : pas de transcodage
        st, cues = self.admin.call("GET", p["subtitle_overlay"] + "?wait=1")
        self.assertEqual(cues["cues"][0]["t"], "Le vent se lève\nsur la montagne")
        # version texte de secours : le dessin vectoriel est écarté, le panneau va en haut
        self.assertEqual([(c["t"], c.get("top", False)) for c in cues["cues"]],
                         [("Le vent se lève\nsur la montagne", True), ("Guilde des aventuriers", True)])
        # script ASS d'origine + polices jointes au MKV, pour le rendu fidèle (libass)
        self.assertEqual(p["subtitle_ass"], f"/api/items/{e1['id']}/subtitles/{ass['id']}/ass")
        st, raw, h = self.admin.call("GET", p["subtitle_ass"], raw=True)
        self.assertEqual(st, 200)
        self.assertIn(b"\\p1}m 0 0 l 60 0", raw)
        st, f = self.admin.call("GET", p["subtitle_ass"].replace("/ass", "/fonts"))
        self.assertIn("dejavu sans", f["wanted"])
        if f["embedded"]:
            self.assertEqual(len(f["fonts"]), 1)
            st, font, h = self.admin.call("GET", f["fonts"][0], raw=True)
            self.assertEqual((st, font[:4]), (200, b"\x00\x01\x00\x00"))
        self.assertEqual(self.admin.call("GET", f"/api/items/{e1['id']}/fonts/font_99.ttf")[0], 404)
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})
        st, e2 = self.admin.call("GET", f"/api/items/{eps[1]['id']}")
        self.assertTrue(e2["subtitles"][0]["overlay"])
        # sous-titres internes SRT et externes
        m = self.movie("Nuit polaire")
        st, d = self.admin.call("GET", f"/api/items/{m['id']}")
        st, c = self.admin.call("GET", f"/api/items/{m['id']}/subtitles/{d['subtitles'][0]['id']}?wait=1")
        self.assertEqual(c["cues"][0]["t"], "Bonjour tout le monde")
        self.assertAlmostEqual(c["cues"][0]["s"], 1000, delta=50)
        st, c = self.admin.call("GET", f"/api/items/{m['id']}/subtitles/{d['subtitles'][1]['id']}")
        self.assertEqual(c["cues"][0]["t"], "Hello everyone")

    def test_progress_continue_next_up_per_user(self):
        show, d, eps = self.episodes("Kaze no Michi", "Animes")
        c = Client(self.base)
        c.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})
        st, p = c.call("POST", "/api/play", {"id": eps[0]["id"]})
        st, r = c.call("POST", "/api/progress", {"id": eps[0]["id"], "position": 40000, "duration": 90000,
                                                  "stream": p["stream"], "state": "playing"})
        st, home = c.call("GET", "/api/home")
        self.assertEqual(home["continue"][0]["id"], eps[0]["id"])
        self.assertEqual({r["library"]["title"] for r in home["rows"]}, {"Films", "Animes"})
        st, r = c.call("POST", "/api/progress", {"id": eps[0]["id"], "position": 88000, "duration": 90000,
                                                  "stream": p["stream"], "state": "stopped"})
        self.assertTrue(r["watched"])
        self.assertIsNone(self.ctx.transcoder.get(p["stream"]))
        st, home = c.call("GET", "/api/home")
        self.assertEqual(home["continue"][0]["id"], eps[1]["id"])
        self.assertTrue(home["continue"][0]["next_up"])
        st, h = c.call("GET", "/api/history")
        self.assertEqual(h["items"][0]["id"], eps[0]["id"])
        st, dd = self.admin.call("GET", f"/api/items/{eps[0]['id']}")
        self.assertFalse(dd["watched"])

    def test_library_permissions(self):
        show, d, eps = self.episodes("Les Veilleurs", "Séries")
        self.assertEqual(self.lea.call("GET", f"/api/libraries/{self.libs['Séries']}/items")[0], 404)
        self.assertEqual(self.lea.call("GET", f"/api/items/{show['id']}")[0], 404)
        self.assertEqual(self.lea.call("POST", "/api/play", {"id": eps[0]["id"]})[0], 404)
        self.assertEqual(self.lea.call("GET", f"/api/img?p={show['thumb']}", raw=True)[0], 404)
        st, me = self.lea.call("GET", "/api/me")
        self.assertEqual([l["title"] for l in me["libraries"]], ["Films", "Animes"])

    def test_mark_show_watched(self):
        show, d, eps = self.episodes("Les Veilleurs", "Séries")
        st, r = self.admin.call("POST", f"/api/items/{d['seasons'][0]['id']}/watched", {"watched": True})
        self.assertEqual(r["count"], 3)
        st, d2 = self.admin.call("GET", f"/api/items/{show['id']}")
        self.assertEqual(d2["watched_count"], 3)
        self.assertEqual(d2["play_target"]["season"], 2)
        self.admin.call("POST", f"/api/items/{show['id']}/watched", {"watched": False})

    def test_mark_watched_from_continue_and_undo(self):
        show, d, eps = self.episodes("Les Veilleurs", "Séries")
        c = self.admin
        st, p = c.call("POST", "/api/play", {"id": eps[0]["id"]})
        st, r = c.call("POST", "/api/progress", {"id": eps[0]["id"], "position": 40000, "duration": 90000,
                                                 "stream": p["stream"], "state": "playing"})
        c.call("POST", "/api/stop", {"stream": p["stream"]})
        st, home = c.call("GET", "/api/home")
        self.assertEqual(home["continue"][0]["id"], eps[0]["id"])
        c.call("POST", f"/api/items/{eps[0]['id']}/watched", {"watched": True})
        st, home = c.call("GET", "/api/home")
        self.assertEqual(home["continue"][0]["id"], eps[1]["id"])  # l'épisode suivant prend la place
        self.assertTrue(home["continue"][0]["next_up"])
        st, h = c.call("GET", "/api/history")
        self.assertEqual(h["items"][0]["id"], eps[0]["id"])
        # « Annuler » : reprise remise à 40 s, vue retirée de l'historique
        c.call("POST", f"/api/items/{eps[0]['id']}/watched", {"watched": False, "undo": True, "position": 40000})
        row = self.ctx.db.one("SELECT watched, position_ms FROM progress WHERE rating_key=?", (str(eps[0]["id"]),))
        self.assertEqual((row["watched"], row["position_ms"]), (0, 40000))  # (fichiers de test de 12 s : pas sur l'accueil)
        st, h = c.call("GET", "/api/history")
        self.assertNotIn(eps[0]["id"], [i["id"] for i in h["items"]])
        c.call("POST", f"/api/items/{show['id']}/watched", {"watched": False})

    def test_suggestions_from_recent_viewing(self):
        from seance import suggest
        self.admin.call("POST", "/api/admin/users", {"username": "sugg", "password": "motdepasse9", "display_name": "Sugg"})
        c = Client(self.base)
        c.call("POST", "/api/login", {"username": "sugg", "password": "motdepasse9"})
        st, home = c.call("GET", "/api/home")
        self.assertEqual((home["suggestions"], home["because"]), ([], []))  # rien regardé : rien à suggérer
        show, d, eps = self.episodes("Les Veilleurs", "Séries")  # série « Thriller »
        c.call("POST", f"/api/items/{eps[0]['id']}/watched", {"watched": True})
        old = suggest.MIN_ROW
        suggest.MIN_ROW = 1  # bibliothèque de test minuscule
        try:
            st, home = c.call("GET", "/api/home")
        finally:
            suggest.MIN_ROW = old
        titles = [i["title"] for i in home["suggestions"]]
        self.assertEqual(titles[0], "Nuit polaire", titles)  # même genre
        self.assertNotIn("Les Veilleurs", titles)  # déjà commencée : pas suggérée
        self.assertEqual(home["because"][0]["seed"]["title"], "Les Veilleurs")
        # fiche : titres similaires, sans historique
        voyage = self.movie("Le Grand Voyage")
        st, r = c.call("GET", f"/api/items/{voyage['id']}/similar")
        self.assertEqual([i["title"] for i in r["items"]][:1], ["Brume"])
        self.assertNotIn("Le Grand Voyage", [i["title"] for i in r["items"]])
        # rangées masquées dans les réglages : pas de calcul
        c.call("PUT", "/api/me/prefs", {"home_hidden": ["suggest", "because"]})
        st, home = c.call("GET", "/api/home")
        self.assertEqual(home["suggestions"], [])

    def test_identify(self):
        m = self.movie("Brume")
        st, r = self.admin.call("GET", f"/api/admin/identify/{m['id']}?q=voyage")
        self.assertEqual(r["results"][0]["tmdb_id"], 5001)
        st, body, h = self.admin.call("GET", r["results"][0]["poster"], raw=True)
        self.assertEqual(st, 200)
        st, r = self.admin.call("POST", f"/api/admin/identify/{m['id']}", {"tmdb_id": 5003})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.lea.call("GET", f"/api/admin/identify/{m['id']}")[0], 403)

    def test_rescan_removes_deleted_files(self):
        d = os.path.join(self.media, "series", "Les Veilleurs", "Season 02")
        moved = os.path.join(self.work, "S02")
        shutil.move(d, moved)
        try:
            self.admin.call("POST", "/api/admin/scan", {"library": self.libs["Séries"]})
            wait_scan(self.admin)
            st, r = self.admin.call("GET", "/api/admin/libraries")
            by = {l["name"]: l for l in r["libraries"]}
            self.assertEqual(by["Séries"]["counts"], {"show": 1, "season": 1, "episode": 3})
        finally:
            shutil.move(moved, d)
            self.admin.call("POST", "/api/admin/scan", {"library": self.libs["Séries"]})
            wait_scan(self.admin)

    def test_unmounted_share_keeps_items(self):
        st, lib = self.admin.call("GET", "/api/admin/libraries")
        root = os.path.join(self.media, "animes")
        hidden = root + "_off"
        os.rename(root, hidden)
        try:
            self.admin.call("POST", "/api/admin/scan", {"library": self.libs["Animes"]})
            s = wait_scan(self.admin)
            self.assertTrue(any("inaccessible" in e for e in s["errors"]))
            st, r = self.admin.call("GET", "/api/admin/libraries")
            by = {l["name"]: l for l in r["libraries"]}
            self.assertEqual(by["Animes"]["counts"].get("episode"), 2)
        finally:
            os.rename(hidden, root)

    def test_browse_and_library_validation(self):
        st, r = self.admin.call("GET", "/api/admin/browse?path=" + self.media)
        self.assertEqual([x for x in r["dirs"] if not x.startswith("_")], ["animes", "films", "series"])
        st, r = self.admin.call("POST", "/api/admin/libraries", {"name": "X", "kind": "movie", "paths": ["/nope"]})
        self.assertEqual(st, 400)
        self.assertEqual(self.lea.call("GET", "/api/admin/browse?path=/")[0], 403)

    # ------------------------------------------------- sous-titres préparés
    def test_subtitles_prepared_in_background_and_on_demand(self):
        show, d, eps = self.episodes("Kaze no Michi", "Animes")
        row = self.ctx.db.one("SELECT * FROM items WHERE id=?", (int(eps[0]["id"]),))
        for _ in range(100):  # préparés en tâche de fond après le scan (aucune lecture en cours)
            if not self.ctx.subtitles.missing(row):
                break
            time.sleep(0.2)
        self.assertEqual(self.ctx.subtitles.missing(row), [])
        # cache vidé : la première demande répond 202 (en préparation) puis 200
        m = self.movie("Nuit polaire")
        mrow = self.ctx.db.one("SELECT * FROM items WHERE id=?", (int(m["id"]),))
        shutil.rmtree(self.ctx.subtitles._dir(mrow), ignore_errors=True)
        st, d = self.admin.call("GET", f"/api/items/{m['id']}")
        url = f"/api/items/{m['id']}/subtitles/{d['subtitles'][0]['id']}"
        codes = []
        for _ in range(100):
            st, r = self.admin.call("GET", url)
            codes.append(st)
            if st == 200:
                break
            self.assertTrue(r["pending"])
            time.sleep(0.1)
        self.assertEqual(codes[-1], 200)
        self.assertEqual(r["cues"][0]["t"], "Bonjour tout le monde")

    def test_progressive_subtitles(self):
        """Gros fichier : la lecture démarre dès que les premières minutes sont prêtes, la suite arrive en route."""
        import seance.routes.media as api_mod  # lecture : fenêtre de préparation des sous-titres
        from seance import subcache
        m = self.movie("Nuit polaire")
        row = self.ctx.db.one("SELECT * FROM items WHERE id=?", (int(m["id"]),))
        st, d = self.admin.call("GET", f"/api/items/{m['id']}")
        sub = d["subtitles"][0]["id"]
        sc = self.ctx.subtitles
        old = (subcache.SOON_READRATE, sc._streaming, api_mod.PREPARE_WINDOW_MS)
        subcache.SOON_READRATE, sc._streaming, api_mod.PREPARE_WINDOW_MS = 6, (lambda: True), 20000
        try:
            shutil.rmtree(sc._dir(row), ignore_errors=True)
            sc.enqueue(row["id"], urgent=True)  # comme à l'ouverture de la fiche : débit plafonné (lecture en cours)
            partial_seen, ready_partial = None, None
            for _ in range(300):
                st, r = self.admin.call("GET", f"/api/items/{m['id']}/subtitles/{sub}")
                if st == 200 and r.get("partial") and r["cues"] and partial_seen is None:
                    partial_seen = r
                if ready_partial is None:
                    st2, pr = self.admin.call("GET", f"/api/items/{m['id']}/prepare?at=0")
                    if pr.get("partial"):
                        ready_partial = pr
                if st == 200 and not r.get("partial"):
                    break
                time.sleep(0.1)
            self.assertIsNotNone(partial_seen, "sous-titres servis pendant l'extraction")
            self.assertLess(partial_seen["until"], 85000)
            self.assertEqual(partial_seen["cues"][0]["t"], "Bonjour tout le monde")
            self.assertTrue(ready_partial and ready_partial["ready"], ready_partial)  # lecture lancée avant la fin
            self.assertFalse(r.get("partial"))  # puis version complète, mise en cache
        finally:
            subcache.SOON_READRATE, sc._streaming, api_mod.PREPARE_WINDOW_MS = old

    def test_renamed_file_keeps_exported_subtitles(self):
        """Un fichier renommé ou réindexé retrouve ses sous-titres déjà exportés : rien n'est relu."""
        m = self.movie("Nuit polaire")
        for _ in range(50):
            st, r = self.admin.call("GET", f"/api/items/{m['id']}/prepare?wait=5")
            if r["ready"]:
                break
        row = self.ctx.db.one("SELECT * FROM items WHERE id=?", (int(m["id"]),))
        self.assertEqual(self.ctx.subtitles.missing(row), [])
        self.assertTrue(os.path.isfile(self.ctx.subtitles.path(row, 3)))  # piste SRT interne exportée
        old, new = row["key"], row["key"].replace(".x264.mkv", ".x264.REPACK.mkv")
        os.rename(old, new)
        try:
            self.admin.call("POST", "/api/admin/scan", {"library": self.libs["Films"]})
            wait_scan(self.admin)
            row2 = self.ctx.db.one("SELECT * FROM items WHERE key=?", (new,))
            self.assertNotEqual(row2["id"], row["id"])     # nouvelle fiche…
            self.assertEqual(self.ctx.subtitles.missing(row2), [])  # … mêmes sous-titres, déjà prêts
            # un second scan sans changement n'écrit rien
            before = self.ctx.db.one("SELECT updated_at, probe FROM items WHERE id=?", (row2["id"],))
            time.sleep(1.1)
            self.admin.call("POST", "/api/admin/scan", {"library": self.libs["Films"]})
            wait_scan(self.admin)
            self.assertEqual(self.ctx.db.one("SELECT updated_at, probe FROM items WHERE id=?", (row2["id"],)), before)
        finally:
            os.rename(new, old)
            self.admin.call("POST", "/api/admin/scan", {"library": self.libs["Films"]})
            wait_scan(self.admin)

    def test_prepare_waits_for_all_subtitles(self):
        m = self.movie("Nuit polaire")
        mrow = self.ctx.db.one("SELECT * FROM items WHERE id=?", (int(m["id"]),))
        shutil.rmtree(self.ctx.subtitles._dir(mrow), ignore_errors=True)
        self.assertTrue(self.ctx.subtitles.missing(mrow))
        for _ in range(20):
            st, r = self.admin.call("GET", f"/api/items/{m['id']}/prepare?wait=5")
            self.assertEqual(st, 200)
            if r["ready"]:
                break
        self.assertTrue(r["ready"])
        self.assertEqual(self.ctx.subtitles.missing(mrow), [])  # toutes les pistes, pas seulement la choisie
        st, r = self.admin.call("GET", f"/api/items/{m['id']}/prepare")
        self.assertEqual(r, {"ready": True, "progress": 1})
        show, d, eps = self.episodes("Kaze no Michi", "Animes")
        self.assertEqual(self.lea.call("GET", f"/api/items/{show['id']}/prepare")[0], 400)

    def test_chapters_markers_and_trickplay(self):
        show, d, eps = self.episodes("Kaze no Michi", "Animes")
        st, e1 = self.admin.call("GET", f"/api/items/{eps[0]['id']}")
        self.assertEqual([c["title"] for c in e1["chapters"]], ["Prologue", "Opening", "Partie A", "Ending", "Preview"])
        self.assertEqual(e1["markers"]["intro"], {"start": 10000, "end": 40000, "source": "chapters"})
        self.assertEqual(e1["markers"]["credits"]["start"], 70000)
        row = self.ctx.db.one("SELECT * FROM items WHERE id=?", (int(eps[0]["id"]),))
        for _ in range(150):
            st, t = self.admin.call("GET", f"/api/items/{eps[0]['id']}/trickplay")
            if t.get("ready"):
                break
            time.sleep(0.2)
        self.assertTrue(t["ready"], t)
        self.assertEqual((t["interval"], t["w"], t["h"], t["sheets"]), (10, 240, 136, 1))
        self.assertIn(t["count"], (9, 10))
        st, body, h = self.admin.call("GET", f"/api/items/{eps[0]['id']}/trickplay/1.jpg", raw=True)
        self.assertEqual(st, 200)
        self.assertEqual(body[:2], b"\xff\xd8")
        self.assertEqual(self.lea.call("GET", f"/api/items/{eps[0]['id']}/trickplay/1.jpg", raw=True)[0], 200)
        self.assertEqual(self.ctx.subtitles.missing(row), [])  # même lecture : sous-titres prêts aussi

    def test_track_prefs_per_show(self):
        show, d, eps = self.episodes("Kaze no Michi", "Animes")
        st, r = self.lea.call("PUT", f"/api/items/{eps[1]['id']}/tracks", {"audio_lang": "jpn", "sub_lang": "off"})
        self.assertEqual(st, 200, r)
        st, sh = self.lea.call("GET", f"/api/items/{show['id']}")
        self.assertEqual(sh["track_prefs"], {"audio_lang": "jpn", "sub_lang": "off", "sub_forced": False})
        self.assertTrue(sh["audio"])  # pistes de l'épisode à lire, pour le choix sur la fiche série
        st, e1 = self.lea.call("GET", f"/api/items/{eps[0]['id']}")
        self.assertEqual(e1["track_prefs"]["sub_lang"], "off")
        st, e1a = self.admin.call("GET", f"/api/items/{eps[0]['id']}")
        self.assertIsNone(e1a["track_prefs"])  # propre à chaque utilisateur
        self.assertEqual(self.lea.call("PUT", f"/api/items/{eps[0]['id']}/tracks", {"audio_lang": "x;y"})[0], 400)
        self.lea.call("PUT", f"/api/items/{eps[0]['id']}/tracks", {"reset": True})

    def test_downmix_51_without_clipping(self):
        from seance.transcoder import DOWNMIX, build_command
        work = tempfile.mkdtemp()
        try:
            src = os.path.join(work, "in.mkv")
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=6", "-f", "lavfi",
                            "-i", "anoisesrc=d=6:a=0.5:c=pink", "-f", "lavfi", "-i", "sine=f=180:d=6", "-filter_complex",
                            # dialogue + ambiance très forts, identiques sur les 6 canaux (cas réel le plus exigeant)
                            "[1][2]amix=inputs=2:normalize=0,volume=1.6,asplit=6[a][b][c][d][e][f];"
                            "[a][b][c][d][e][f]join=inputs=6:channel_layout=5.1[o]",
                            "-map", "0:v", "-map", "[o]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "ac3", src],
                           check=True, timeout=60)
            from seance import media
            info = media.probe(src)
            p = plan(info)
            self.assertEqual(p["mode"], "copy")
            self.assertFalse(p["acopy"])
            self.assertIn("audio AC3 6 canaux mixé en stéréo", p["reasons"])
            out = os.path.join(work, "out")
            os.makedirs(out)
            cmd = build_command(src, p, 0, out)
            self.assertTrue(any(DOWNMIX in c for c in cmd), cmd)
            subprocess.run(cmd, check=True, timeout=60)
            with open(os.path.join(out, "all.mp4"), "wb") as f:
                for name in ["init.mp4"] + sorted((x for x in os.listdir(out) if x.endswith(".m4s")),
                                                  key=lambda x: int(x.split(".")[0])):
                    with open(os.path.join(out, name), "rb") as g:
                        f.write(g.read())
            st = subprocess.run(["ffmpeg", "-hide_banner", "-i", os.path.join(out, "all.mp4"), "-map", "0:a",
                                 "-af", "astats", "-f", "null", "-"], capture_output=True, timeout=60).stderr.decode()
            overall = st.split("Overall")[-1]
            peak = float(overall.split("Peak level dB:")[1].split()[0])
            # sans protection, ce 5.1 dépasse largement 0 dB (saturation) ; marge pour le codage AAC
            self.assertLess(peak, 0.5)
            # même une piste AAC 5.1 n'est plus copiée telle quelle
            info["audio"][0].update(codec="aac", channels=6)
            self.assertFalse(plan(info)["acopy"])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # ---------------------------------------------------- détection des nouveautés
    def test_watcher_detects_new_episode_and_waits_for_copy(self):
        from seance import scanner
        lib = self.libs["Séries"]
        season = os.path.join(self.media, "series", "Les Veilleurs", "Season 01")
        src = os.path.join(season, "Les.Veilleurs.S01E01.720p.WEB.mkv")
        new = os.path.join(season, "Les.Veilleurs.S01E04.720p.WEB.mkv")
        w = self.ctx.watcher
        self.ctx.db.set_setting("watch_interval", 1)
        old_recent = scanner.RECENT_SECONDS
        w.prints.clear(); w.last_check.clear(); w.pending.clear()
        try:
            t = time.time()
            w.tick(t)                       # référence
            scanner.RECENT_SECONDS = 45
            shutil.copy(src, new)           # « copie en cours » : fichier tout récent
            w.tick(t + 2)                   # changement détecté
            self.assertIn(int(lib), w.pending)
            self.assertEqual(w.pending[int(lib)]["dirs"], {season})  # seul le dossier de la saison a bougé
            w.tick(t + 4)                   # dossiers stables : scan
            st, sc = self.admin.call("GET", "/api/admin/scan")
            self.assertTrue(sc.get("partial"), sc)  # scan partiel : uniquement ce dossier
            wait_scan(self.admin)
            st, r = self.admin.call("GET", "/api/admin/libraries")
            by = {l["name"]: l for l in r["libraries"]}
            self.assertEqual(by["Séries"]["counts"]["episode"], 4)  # fichier trop récent : ignoré
            self.assertIn(int(lib), w.pending)                   # … mais un nouveau passage est prévu
            os.utime(new, (t - 600, t - 600))                     # copie terminée
            w.tick(t + 6)
            wait_scan(self.admin)
            st, r = self.admin.call("GET", "/api/admin/libraries")
            by = {l["name"]: l for l in r["libraries"]}
            self.assertEqual(by["Séries"]["counts"]["episode"], 5)
        finally:
            scanner.RECENT_SECONDS = old_recent
            self.ctx.db.set_setting("watch_interval", 0)
            os.remove(new)
            self.admin.call("POST", "/api/admin/scan", {"library": lib})
            wait_scan(self.admin)

    def test_watcher_show_moved_between_libraries(self):
        """Une série déplacée de « Séries » vers « Animes » : scan partiel des deux côtés, sans toucher au reste."""
        src = os.path.join(self.media, "series", "Les Veilleurs")
        dst = os.path.join(self.media, "animes", "Les Veilleurs")
        w = self.ctx.watcher
        self.ctx.db.set_setting("watch_interval", 1)

        def counts():
            st, r = self.admin.call("GET", "/api/admin/libraries")
            return {l["name"]: l["counts"] for l in r["libraries"]}

        w.prints.clear(); w.last_check.clear(); w.pending.clear()
        try:
            t = time.time()
            w.tick(t)                       # référence
            shutil.move(src, dst)
            w.tick(t + 2)                   # changement détecté dans les deux bibliothèques
            self.assertIn(int(self.libs["Séries"]), w.pending)
            self.assertIn(int(self.libs["Animes"]), w.pending)
            w.tick(t + 4)                   # stable : scan partiel
            st, sc = self.admin.call("GET", "/api/admin/scan")
            self.assertTrue(sc.get("partial"), sc)
            wait_scan(self.admin)
            for k in range(3):              # au cas où une bibliothèque attendait la fin du scan précédent
                w.tick(t + 6 + 2 * k)
                wait_scan(self.admin)
            c = counts()
            self.assertEqual(c["Animes"].get("show"), 2, c)       # Kaze no Michi toujours là + Les Veilleurs
            self.assertEqual(c["Animes"].get("episode"), 6, c)
            self.assertEqual(c["Séries"].get("episode", 0), 0, c)  # plus rien côté Séries
            self.assertEqual(c["Séries"].get("show", 0), 0, c)
        finally:
            self.ctx.db.set_setting("watch_interval", 0)
            if os.path.isdir(dst):
                shutil.move(dst, src)
            for name in ("Séries", "Animes"):
                self.admin.call("POST", "/api/admin/scan", {"library": self.libs[name]})
                wait_scan(self.admin)

    def test_watcher_cleans_origin_library_even_if_it_saw_nothing(self):
        """Origine du déplacement « muette » (cache SMB…) : l'arrivée dans Animes fait vérifier Séries."""
        from seance import watcher as wmod
        src = os.path.join(self.media, "series", "Les Veilleurs")
        dst = os.path.join(self.media, "animes", "Les Veilleurs")
        w = self.ctx.watcher
        self.ctx.db.set_setting("watch_interval", 1)
        series, animes = int(self.libs["Séries"]), int(self.libs["Animes"])
        w.prints.clear(); w.last_check.clear(); w.pending.clear()
        try:
            t = time.time()
            w.tick(t)
            shutil.move(src, dst)
            w.prints[series] = wmod.snapshot([os.path.join(self.media, "series")])  # Séries ne « voit » rien
            w.last_check[series] = t + 2
            w.tick(t + 2)
            self.assertIn(animes, w.pending)
            self.assertIn(series, w.pending)            # dossier indexé disparu repéré
            self.assertIn(src, w.pending[series]["dirs"])
            for k in range(4):
                w.tick(t + 4 + 2 * k)
                wait_scan(self.admin)
            st, r = self.admin.call("GET", "/api/admin/libraries")
            c = {l["name"]: l["counts"] for l in r["libraries"]}
            self.assertEqual(c["Séries"].get("episode", 0), 0, c)
            self.assertEqual(c["Animes"].get("show"), 2, c)
        finally:
            self.ctx.db.set_setting("watch_interval", 0)
            if os.path.isdir(dst):
                shutil.move(dst, src)
            for name in ("Séries", "Animes"):
                self.admin.call("POST", "/api/admin/scan", {"library": self.libs[name]})
                wait_scan(self.admin)

    def test_webhook(self):
        c = Client(self.base)
        self.assertEqual(c.call("POST", "/api/hooks/scan?token=faux", {"eventType": "Download"}, csrf=False)[0], 403)
        st, s = self.admin.call("GET", "/api/admin/settings")
        path = s["webhook_path"]
        st, r = c.call("POST", path, {"eventType": "Test"}, csrf=False)
        self.assertEqual(st, 200)
        self.assertIn("test", r["message"])
        self.ctx.watcher.force_at = None
        st, r = c.call("POST", path, {"eventType": "Download", "movie": {"title": "X"}}, csrf=False)
        self.assertEqual(st, 200)
        self.assertIsNotNone(self.ctx.watcher.force_at)
        self.assertIsNone(self.ctx.watcher.force_scope)  # dossier inconnu : scan complet
        self.ctx.watcher.force_at = None
        # Sonarr cite ses propres chemins (/tv/…) : Nexis retrouve le dossier local et ne revoit que lui
        st, r = c.call("POST", path, {"eventType": "Download", "series": {"title": "Les Veilleurs", "path": "/tv/Les Veilleurs"},
                                      "episodeFile": {"path": "/tv/Les Veilleurs/Season 01/Les.Veilleurs.S01E01.mkv"}},
                       csrf=False)
        self.assertTrue(r["partial"])
        show_dir = os.path.join(self.media, "series", "Les Veilleurs")
        self.assertEqual(self.ctx.watcher.force_scope[int(self.libs["Séries"])] >= {show_dir, os.path.join(show_dir, "Season 01")},
                         True)
        self.ctx.watcher.force_at, self.ctx.watcher.force_scope = None, None
        st, s2 = self.admin.call("POST", "/api/admin/webhook/regenerate")
        self.assertNotEqual(s2["webhook_path"], path)
        self.assertEqual(c.call("POST", path, {}, csrf=False)[0], 403)

    # ------------------------------------------------ collections, bonus, listes
    def test_collections_extras_trailer(self):
        st, cols = self.admin.call("GET", f"/api/libraries/{self.libs['Films']}/collections")
        self.assertEqual([(c["title"], c["leaves"], c["kind"]) for c in cols], [("Saga Démo", 2, "auto")])
        st, c = self.admin.call("GET", f"/api/collections/{cols[0]['id']}")
        self.assertEqual([x["title"] for x in c["items"]], ["Le Grand Voyage", "Brume"])
        self.assertEqual(c["summary"], "Deux films de démonstration.")
        st, body, h = self.admin.call("GET", f"/api/img?p={cols[0]['thumb']}&w=200&h=300", raw=True)
        self.assertEqual(st, 200)
        m = self.movie("Le Grand Voyage")
        st, d = self.admin.call("GET", f"/api/items/{m['id']}")
        self.assertEqual(d["trailer"], "abcDEF12345")
        self.assertEqual([c["title"] for c in d["collections"]], ["Saga Démo"])
        self.assertEqual(sorted((e["extra_kind"], e["title"]) for e in d["extras"]),
                         [("featurette", "Le Grand Voyage"), ("trailer", "Bande annonce officielle")])
        self.assertEqual(len(self.items("Films")), 3)  # les bonus ne sont pas des films
        trailer = next(e for e in d["extras"] if e["extra_kind"] == "trailer")
        st, p = self.admin.call("POST", "/api/play", {"id": trailer["id"]})
        self.assertEqual(st, 200, p)
        self.admin.call("POST", "/api/progress", {"id": trailer["id"], "position": 7000, "duration": 8000,
                                                   "stream": p["stream"], "state": "stopped"})
        self.assertIsNone(self.ctx.db.one("SELECT 1 FROM progress WHERE rating_key=?", (trailer["id"],)))
        # collection manuelle (admin)
        self.assertEqual(self.lea.call("POST", "/api/admin/collections", {"title": "X", "item_id": m["id"]})[0], 403)
        st, r = self.admin.call("POST", "/api/admin/collections", {"title": "Mes favoris", "item_id": m["id"]})
        cid = r["id"]
        n = self.movie("Nuit polaire")
        self.assertEqual(self.admin.call("POST", f"/api/admin/collections/{cid}/items", {"item_id": n["id"]})[0], 200)
        st, c = self.lea.call("GET", f"/api/collections/{cid}")
        self.assertEqual(len(c["items"]), 2)
        self.admin.call("DELETE", f"/api/admin/collections/{cid}/items/{n['id']}")
        self.admin.call("DELETE", f"/api/admin/collections/{cid}")
        self.assertEqual(self.lea.call("GET", f"/api/collections/{cid}")[0], 404)

    def test_watchlist_and_playlists(self):
        m = self.movie("Nuit polaire")
        show, d, eps = self.episodes("Kaze no Michi", "Animes")
        c = Client(self.base)
        c.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})
        c.call("POST", f"/api/items/{m['id']}/watchlist", {"on": True})
        c.call("POST", f"/api/items/{eps[1]['id']}/watchlist", {"on": True})  # un épisode -> sa série
        st, wl = c.call("GET", "/api/watchlist")
        self.assertEqual({x["title"] for x in wl}, {"Nuit polaire", "Kaze no Michi"})
        st, home = c.call("GET", "/api/home")
        self.assertEqual(len(home["watchlist"]), 2)
        st, di = c.call("GET", f"/api/items/{m['id']}")
        self.assertTrue(di["in_watchlist"])
        c.call("POST", f"/api/items/{m['id']}/watched", {"watched": True})  # vu : retiré de la liste
        st, wl = c.call("GET", "/api/watchlist")
        self.assertEqual([x["title"] for x in wl], ["Kaze no Michi"])
        c.call("POST", f"/api/items/{m['id']}/watched", {"watched": False})
        # playlists
        st, r = c.call("POST", "/api/playlists", {"title": "Soirée", "item_id": show["id"]})
        pid = r["id"]
        c.call("POST", f"/api/playlists/{pid}/items", {"item_id": m["id"]})
        st, pl = c.call("GET", f"/api/playlists/{pid}")
        self.assertEqual([x["title"] for x in pl["items"]], ["Titre 1x1", "Titre 1x2", "Nuit polaire"])
        st, r = c.call("POST", "/api/play", {"id": eps[1]["id"], "playlist": pid})
        self.assertEqual(r["next"]["title"], "Nuit polaire")  # suivant = playlist, pas la série
        c.call("POST", "/api/stop", {"stream": r["stream"]})
        order = [pl["items"][2]["entry_id"], pl["items"][0]["entry_id"], pl["items"][1]["entry_id"]]
        c.call("PUT", f"/api/playlists/{pid}", {"order": order, "title": "Soirée ciné"})
        st, pl = c.call("GET", f"/api/playlists/{pid}")
        self.assertEqual((pl["title"], pl["items"][0]["title"]), ("Soirée ciné", "Nuit polaire"))
        self.assertEqual(self.admin.call("GET", f"/api/playlists/{pid}")[0], 404)  # propre à l'utilisateur
        c.call("DELETE", f"/api/playlists/{pid}/items/{pl['items'][0]['entry_id']}")
        st, lists = c.call("GET", "/api/playlists")
        self.assertEqual(lists[0]["count"], 2)
        c.call("DELETE", f"/api/playlists/{pid}")
        c.call("POST", f"/api/items/{show['id']}/watchlist", {"on": False})

    # ------------------------------------------------ Découvrir et demandes (Seerr)
    def test_discover_and_requests_through_seerr(self):
        seerr = FakeSeerr()
        try:
            # sans Seerr : catalogue visible, pas de demande possible
            st, home = self.lea.call("GET", "/api/discover/home")
            self.assertEqual(st, 200, home)
            self.assertFalse(home["seerr"])
            self.assertEqual([p["name"] for p in home["providers"]], ["Netflix", "Prime Video"])
            nf = home["providers"][0]["items"]
            self.assertEqual([(c["title"], c["status"]) for c in nf],
                             [("Série Netflix", None), ("Film Netflix", None),
                              ("Kaze no Michi", "available"), ("Le Grand Voyage", "available")])
            self.assertEqual(nf[3]["item_id"], self.movie("Le Grand Voyage")["id"])
            self.assertEqual(len(home["trending"]), 4)  # les personnes sont écartées
            self.assertEqual(self.lea.call("POST", "/api/discover/request", {"type": "movie", "tmdb_id": 9101})[0], 409)
            st, body, h = self.lea.call("GET", "/api/discover/img?p=/p9101.jpg&k=poster", raw=True)
            self.assertEqual((st, body[:2]), (200, b"\xff\xd8"))
            self.assertEqual(self.lea.call("GET", "/api/discover/img?p=../etc/passwd")[0], 400)
            # configuration (admin)
            self.assertEqual(self.lea.call("PUT", "/api/admin/requests", {"seerr_url": seerr.url})[0], 403)
            st, cfg = self.admin.call("PUT", "/api/admin/requests", {"seerr_url": seerr.url + "/api/v1",
                                                                     "seerr_key": seerr.key})
            self.assertEqual((st, cfg["enabled"], cfg["seerr_url"]), (200, True, seerr.url))
            self.assertNotIn("seerr_key", cfg)
            st, t = self.admin.call("POST", "/api/admin/requests/test")
            self.assertEqual((st, t["media_server"]), (200, "Jellyfin"), t)
            # fiche d'un titre du catalogue, puis demande au nom de Léa (même nom dans Seerr)
            st, title = self.lea.call("GET", "/api/discover/title/tv/9201")
            self.assertEqual([x["number"] for x in title["seasons"]], [1, 2])
            self.assertEqual((title["trailer"], title["streams"][0]["name"], title["can_request"]),
                             ("trailer9999", "Netflix", True))
            st, r = self.lea.call("POST", "/api/discover/request", {"type": "tv", "tmdb_id": 9201, "seasons": [1]})
            self.assertEqual((st, r["status"], r["as_user"]), (200, "pending", True), r)
            lea_seerr = next(u for u in seerr.users if u.get("username") == "lea")
            self.assertEqual(seerr.requests[-1], {"user": lea_seerr["id"], "mediaType": "tv", "mediaId": 9201,
                                                  "seasons": [1]})
            st, title = self.lea.call("GET", "/api/discover/title/tv/9201")
            self.assertEqual((title["status"], title["can_request"]), ("pending", False))
            self.assertEqual(self.lea.call("POST", "/api/discover/request", {"type": "tv", "tmdb_id": 9201})[0], 502)
            # « À regarder » d'un titre absent : demandé automatiquement
            st, r = self.lea.call("POST", "/api/wishlist", {"type": "movie", "tmdb_id": 9101, "on": True})
            self.assertEqual((st, r["requested"]), (200, True), r)
            self.assertEqual(seerr.requests[-1]["mediaId"], 9101)
            st, wl = self.lea.call("GET", "/api/wishlist")
            self.assertEqual([(c["title"], c["status"], c["wished"]) for c in wl], [("Film Netflix", "pending", True)])
            # un titre déjà présent rejoint directement la liste « À regarder » habituelle
            st, r = self.lea.call("POST", "/api/wishlist", {"type": "movie", "tmdb_id": 5001, "on": True})
            self.assertEqual(r["item_id"], self.movie("Le Grand Voyage")["id"])
            st, wl2 = self.lea.call("GET", "/api/watchlist")
            self.assertIn("Le Grand Voyage", [c["title"] for c in wl2])
            # recherche
            st, sr = self.lea.call("GET", "/api/discover/search?q=netflix")
            self.assertEqual({c["title"] for c in sr["results"]}, {"Film Netflix", "Série Netflix"})
            # arrivée du film dans la bibliothèque : il passe de l'envie à la vraie liste
            row = self.ctx.db.one("SELECT * FROM items WHERE title='Nuit polaire'")
            self.ctx.db.run("INSERT INTO wishlist(user_id,media_type,tmdb_id,title,added_at) "
                            "SELECT id,'movie',5002,'Nuit polaire',1 FROM users WHERE username='lea'")
            self.ctx.metadata.refresh(row, tmdb_id=5002)
            st, wl2 = self.lea.call("GET", "/api/watchlist")
            self.assertIn("Nuit polaire", [c["title"] for c in wl2])
            self.assertEqual(self.ctx.db.scalar("SELECT COUNT(*) FROM wishlist WHERE tmdb_id=5002"), 0)
            for c in wl2:
                self.lea.call("POST", f"/api/items/{c['id']}/watchlist", {"on": False})
            self.lea.call("POST", "/api/wishlist", {"type": "movie", "tmdb_id": 9101, "on": False})
        finally:
            self.admin.call("PUT", "/api/admin/requests", {"seerr_url": "", "seerr_key": ""})
            seerr.httpd.shutdown()

    # ------------------------------------------------- profils et contrôle parental
    def test_profiles_pin_and_parental_control(self):
        st, kid = self.admin.call("POST", "/api/admin/users", {
            "username": "tom", "display_name": "Tom", "managed": True, "pin": "1234", "max_age": 10,
            "allow_unrated": False})
        self.assertEqual(st, 200, kid)
        self.assertTrue(kid["managed"] and kid["has_pin"])
        self.assertEqual(self.admin.call("POST", "/api/admin/users", {"username": "x1", "managed": True, "is_admin": True})[0], 400)
        try:
            # un profil géré ne se connecte pas par mot de passe
            c = Client(self.base)
            self.assertEqual(c.call("POST", "/api/login", {"username": "tom", "password": ""})[0], 401)
            # depuis la session de l'admin : « Qui regarde ? »
            dev = Client(self.base)
            dev.call("POST", "/api/login", {"username": "colas", "password": "motdepasse1"})
            st, profiles = dev.call("GET", "/api/profiles")
            by = {p["display_name"]: p for p in profiles}
            self.assertEqual(by["Tom"]["needs"], "pin")
            self.assertEqual(by["colas"]["needs"], "password")
            self.assertTrue(by["colas"]["current"])
            self.assertEqual(dev.call("POST", f"/api/profiles/{kid['id']}/switch", {"pin": "0000"})[0], 400)
            st, r = dev.call("POST", f"/api/profiles/{kid['id']}/switch", {"pin": "1234"})
            self.assertEqual(st, 200, r)
            st, me = dev.call("GET", "/api/me")
            self.assertEqual(me["user"]["display_name"], "Tom")
            # contrôle parental : films « U » visibles, séries classées 12 masquées partout
            st, r = dev.call("GET", f"/api/libraries/{self.libs['Films']}/items")
            self.assertEqual(r["total"], 3)
            st, r = dev.call("GET", f"/api/libraries/{self.libs['Animes']}/items")
            self.assertEqual(r["total"], 0)
            show, d, eps = self.episodes("Kaze no Michi", "Animes")
            self.assertEqual(dev.call("GET", f"/api/items/{show['id']}")[0], 404)
            self.assertEqual(dev.call("GET", f"/api/items/{eps[0]['id']}")[0], 404)
            self.assertEqual(dev.call("POST", "/api/play", {"id": eps[0]["id"]})[0], 404)
            st, r = dev.call("GET", "/api/search?q=kaze")
            self.assertEqual(r["shows"] + r["episodes"], [])
            st, home = dev.call("GET", "/api/home")
            self.assertNotIn("Animes", [row["library"]["title"] for row in home["rows"]])
            # retour sur le compte admin : mot de passe exigé (pas de PIN)
            self.assertEqual(dev.call("POST", f"/api/profiles/{by['colas']['id']}/switch", {"password": "faux"})[0], 400)
            st, r = dev.call("POST", f"/api/profiles/{by['colas']['id']}/switch", {"password": "motdepasse1"})
            self.assertEqual(st, 200)
            st, me = dev.call("GET", "/api/me")
            self.assertEqual(me["user"]["username"], "colas")
            # PIN personnel
            self.assertEqual(dev.call("PUT", "/api/me/pin", {"pin": "12", "password": "motdepasse1"})[0], 400)
            self.assertEqual(dev.call("PUT", "/api/me/pin", {"pin": "4321", "password": "faux"})[0], 400)
            self.assertEqual(dev.call("PUT", "/api/me/pin", {"pin": "4321", "password": "motdepasse1"})[0], 200)
            st, profiles = dev.call("GET", "/api/profiles")
            self.assertEqual({p["display_name"]: p for p in profiles}["colas"]["needs"], "pin")
            dev.call("PUT", "/api/me/pin", {"pin": "", "password": "motdepasse1"})
        finally:
            self.ctx.throttle.fails.clear()
            self.admin.call("DELETE", f"/api/admin/users/{kid['id']}")

    # ------------------------------------------------------------- auth / admin
    def test_setup_locked_and_csrf(self):
        c = Client(self.base)
        st, r = c.call("POST", "/api/setup", {"username": "evil", "password": "xxxxxxxxxx"},
                       headers={"X-Setup-Code": "WHATEVER"})
        self.assertEqual(st, 403)
        self.assertEqual(c.call("GET", "/api/home")[0], 401)
        self.assertEqual(c.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"}, csrf=False)[0], 403)

    def test_throttle(self):
        c = Client(self.base)
        codes = [c.call("POST", "/api/login", {"username": "bob", "password": "nope"})[0] for _ in range(9)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8], 429)
        self.ctx.throttle.fails.clear()

    def test_prefs_and_admin_guards(self):
        st, p = self.lea.call("PUT", "/api/me/prefs", {"accent": "#ff0055"})
        self.assertEqual(p["accent"], "#ff0055")
        self.assertEqual(self.lea.call("PUT", "/api/me/prefs", {"accent": "red;}"})[0], 400)
        # ordre de l'accueil et de la barre du haut
        st, p = self.lea.call("PUT", "/api/me/prefs", {"home_order": ["lib:3", "continue", "together"],
                                                        "nav_order": ["live", "home"], "nav_hidden": ["watchlist"]})
        self.assertEqual((st, p["home_order"], p["nav_hidden"]), (200, ["lib:3", "continue", "together"], ["watchlist"]))
        for bad in ({"home_order": ["<script>"]}, {"nav_order": ["home", "home"]}, {"nav_hidden": "live"},
                    {"home_hidden": [3]}):
            self.assertEqual(self.lea.call("PUT", "/api/me/prefs", bad)[0], 400, bad)
        self.lea.call("PUT", "/api/me/prefs", {"home_order": [], "nav_order": [], "nav_hidden": []})
        self.assertEqual(self.lea.call("GET", "/api/admin/users")[0], 403)
        st, users = self.admin.call("GET", "/api/admin/users")
        me = [u for u in users if u["username"] == "colas"][0]
        self.assertEqual(self.admin.call("DELETE", f"/api/admin/users/{me['id']}")[0], 400)

    def test_admin_update_check(self):
        from unittest import mock
        from seance import __version__, update
        with mock.patch.object(update, "check_latest", lambda: {"latest": "99.0.0", "changes": [{"title": "Nouveau"}], "source": "x/y (main)"}):
            st, u = self.admin.call("GET", "/api/admin/update")
        self.assertEqual((st, u["current"], u["available"]), (200, __version__, True))
        self.assertFalse(u["can_update"])  # pas de service root dans les tests
        self.assertEqual(self.lea.call("GET", "/api/admin/update")[0], 403)
        self.assertEqual(self.lea.call("POST", "/api/admin/update")[0], 403)

    def test_offline_downloads(self):
        """Téléchargements hors ligne : MP4 lisible tel quel envoyé direct, MKV préparé en MP4, reprise par plages,
        positions hors ligne renvoyées au retour du réseau."""
        voyage, polar = self.movie("Le Grand Voyage"), self.movie("Nuit polaire")
        st, j = self.admin.call("POST", "/api/downloads", {"id": voyage["id"], "quality": "original"})
        self.assertEqual((st, j["state"], j["mode"]), (200, "ready", "original"), j)
        st, part, h = self.admin.call("GET", f"/api/downloads/{j['id']}/file", raw=True, headers={"Range": "bytes=0-99"})
        self.assertEqual((st, len(part), h["Content-Type"]), (206, 100, "video/mp4"))
        self.assertEqual(self.lea.call("GET", f"/api/downloads/{j['id']}")[0], 404)  # le téléchargement d'un autre
        # MKV : préparé en MP4 (son AC3 converti), une étape à la fois
        st, j = self.admin.call("POST", "/api/downloads", {"id": polar["id"], "quality": "original"})
        self.assertIn(j["state"], ("queued", "preparing", "ready"))
        for _ in range(120):
            st, j = self.admin.call("GET", f"/api/downloads/{j['id']}")
            if j["state"] in ("ready", "error"):
                break
            time.sleep(0.5)
        self.assertEqual(j["state"], "ready", j)
        st, data, h = self.admin.call("GET", f"/api/downloads/{j['id']}/file", raw=True)
        self.assertEqual(len(data), j["size"])
        self.assertEqual(ffprobe_codec(data), "h264")
        self.admin.call("DELETE", f"/api/downloads/{j['id']}")
        self.assertEqual(self.admin.call("GET", f"/api/downloads/{j['id']}")[0], 404)
        # le dialogue annonce ce que coûte chaque qualité : ici, « auto » se contente d'une copie
        st, o = self.admin.call("POST", "/api/downloads/options", {"id": polar["id"]})
        auto = next(x for x in o["options"] if x["quality"] == "auto")
        self.assertEqual((st, auto["convert"]), (200, False), o)
        self.assertGreater(auto["size"], 0)
        self.assertEqual(self.admin.call("POST", "/api/downloads", {"id": polar["id"], "burn": 999})[0], 400)
        # positions enregistrées hors ligne : appliquées, sauf si on a regardé ailleurs depuis
        now = int(time.time())
        st, r = self.admin.call("POST", "/api/progress/offline", {"items": [
            {"id": polar["id"], "position": 40000, "duration": 90000, "at": now},
            {"id": voyage["id"], "position": 30000, "duration": 90000, "at": now - 10 ** 6}]})
        self.assertEqual(st, 200)
        row = self.ctx.db.one("SELECT position_ms FROM progress WHERE user_id=1 AND rating_key=?", (str(polar["id"]),))
        self.assertEqual(row["position_ms"], 40000)
        # désactivé par l'administrateur
        self.ctx.db.set_setting("downloads", "0")
        try:
            self.assertEqual(self.admin.call("POST", "/api/downloads", {"id": voyage["id"]})[0], 403)
        finally:
            self.ctx.db.set_setting("downloads", "1")

    def test_web_app_install(self):
        """Web app installable : manifeste, icônes aux bonnes tailles, service worker généré et versionné."""
        import io
        from PIL import Image
        st, body, h = self.admin.call("GET", "/manifest.webmanifest", raw=True)
        self.assertEqual((st, h["Content-Type"]), (200, "application/manifest+json"))
        m = json.loads(body)
        self.assertEqual((m["display"], m["start_url"], m["scope"]), ("standalone", "/", "/"))
        purposes = {i["purpose"] for i in m["icons"]}
        self.assertEqual(purposes, {"any", "maskable"})
        for icon in m["icons"]:
            st, data, _ = self.admin.call("GET", icon["src"], raw=True)
            self.assertEqual(st, 200, icon["src"])
            w, hgt = Image.open(io.BytesIO(data)).size
            self.assertEqual(f"{w}x{hgt}", icon["sizes"], icon["src"])
        st, sw, h = self.admin.call("GET", "/sw.js", raw=True)
        code = sw.decode()
        self.assertEqual((st, h["Cache-Control"]), (200, "no-cache"))
        self.assertNotIn("const VERSION = 'dev'", code)
        shell = json.loads(re.search(r"^const SHELL = (.*);$", code, re.M).group(1))
        self.assertIn("/js/app.js", shell)
        self.assertIn("/js/views/home.js", shell)
        self.assertFalse([u for u in shell if u.startswith(("/api/", "/vendor/"))])
        for u in shell:  # tout ce qui est mis en cache existe (sinon l'installation du service worker échoue)
            self.assertEqual(self.admin.call("GET", u, raw=True)[0], 200, u)
        self.assertEqual(self.admin.call("GET", "/sw.js", raw=True, headers={"If-None-Match": h["ETag"]})[0], 304)

    def test_static(self):
        st, body, h = self.admin.call("GET", "/", raw=True)
        self.assertIn(b"<!doctype html>", body.lower())
        self.assertEqual(self.admin.call("GET", "/../seance/api.py", raw=True)[0], 404)
        # compression et revalidation : la revisite ne retélécharge rien
        st, body, h = self.admin.call("GET", "/js/player.js", raw=True, headers={"Accept-Encoding": "gzip"})
        self.assertEqual((st, h.get("Content-Encoding")), (200, "gzip"))
        import gzip as gz
        self.assertIn(b"renderPlayer", gz.decompress(body))
        self.assertEqual(self.admin.call("GET", "/js/player.js", raw=True,
                                         headers={"If-None-Match": h["ETag"]})[0], 304)
        st, body, h2 = self.admin.call("GET", "/js/player.js", raw=True)
        self.assertNotIn("Content-Encoding", h2)  # client sans gzip : fichier tel quel
        self.assertEqual(self.admin.call("GET", "/img/nexis-logo.webp", raw=True)[2].get("Content-Encoding"), None)


class UnitTests(unittest.TestCase):
    def test_download_prep_yields_to_viewers(self):
        """La préparation d'un téléchargement se met en pause tant que quelqu'un regarde."""
        from seance import downloads as dl
        viewing = {"v": True}
        mgr = dl.Downloads.__new__(dl.Downloads)
        mgr.ctx = types.SimpleNamespace(viewing=lambda: viewing["v"])
        job = types.SimpleNamespace(waiting=False)
        proc = subprocess.Popen(["sleep", "30"])
        old = dl.time.sleep
        try:
            t = threading.Thread(target=mgr._yield_to_viewers, args=(job, proc), daemon=True)
            t.start()
            state = lambda: open(f"/proc/{proc.pid}/stat").read().split(") ")[1][0]
            for _ in range(50):
                if state() == "T":
                    break
                time.sleep(0.1)
            self.assertEqual((state(), job.waiting), ("T", True))  # arrêtée pendant la lecture
            viewing["v"] = False
            for _ in range(60):
                if state() != "T":
                    break
                time.sleep(0.1)
            self.assertNotEqual(state(), "T")  # reprend ensuite
            self.assertFalse(job.waiting)
        finally:
            proc.kill()
            proc.wait()

    def test_update_source_and_command(self):
        """Mise à jour depuis l'interface : source validée, commande lancée sans rien venant de Nexis."""
        from unittest import mock
        from seance import update
        work = tempfile.mkdtemp()
        src = os.path.join(work, "source")
        with mock.patch.object(update, "SOURCE", src):
            self.assertEqual(update.read_source(), ("xenix13", "Nexis", "main"))  # installation ancienne : défaut
            with open(src, "w") as f:
                f.write("SEANCE_REPO=https://github.com/xenix13/Nexis.git\nSEANCE_BRANCH=test\n")
            self.assertEqual(update.read_source(), ("xenix13", "Nexis", "test"))
            for repo, branch in (("https://evil.example/x/y.git", "main"), ("https://github.com/a/b", "main; rm -rf /"),
                                 ("https://github.com/a/b", "../../etc")):
                with open(src, "w") as f:
                    f.write(f"SEANCE_REPO={repo}\nSEANCE_BRANCH={branch}\n")
                with self.assertRaises(update.UpdateError):
                    update.read_source()
            with open(src, "w") as f:
                f.write("SEANCE_REPO=https://github.com/xenix13/Nexis\nSEANCE_BRANCH=main\n")
            calls = []

            def fake_run(cmd, **kw):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 3 if cmd[0] == "systemctl" else 0, b"", b"")
            with mock.patch.object(update.subprocess, "run", fake_run):
                self.assertEqual(update.start(), {"started": True})
            run = calls[-1]
            self.assertEqual(run[:2], ["systemd-run", "--unit=seance-update"])
            self.assertIn("https://raw.githubusercontent.com/xenix13/Nexis/main/deploy/install.sh", run[-1])
            self.assertIn("SEANCE_BRANCH=main", run[-1])
        self.assertTrue(update.newer("3.10.0", "3.9.2"))
        self.assertFalse(update.newer("3.6.1", "3.6.1"))
        shutil.rmtree(work, ignore_errors=True)

    def test_throttle_pauses_ffmpeg_ahead(self):
        from seance import transcoder as tr
        work = tempfile.mkdtemp()
        t = tr.Transcoder(os.path.join(work, "t"), lambda: (4, 2))
        old = (tr.AHEAD_PAUSE, tr.AHEAD_RESUME, tr.KEEP_BEHIND)
        tr.AHEAD_PAUSE, tr.AHEAD_RESUME, tr.KEEP_BEHIND = 5, 3, 2
        try:
            s = tr.Session("abcdefgh", "/x", {"mode": "copy"}, [(i * 6.0, 6.0) for i in range(20)],
                           os.path.join(work, "t", "abcdefgh"))
            os.makedirs(s.dir)
            for i in range(12):
                open(os.path.join(s.dir, f"{i}.m4s"), "wb").close()
            s.proc = subprocess.Popen(["sleep", "30"])
            t.sessions[s.sid] = s
            state = lambda: open(f"/proc/{s.proc.pid}/stat").read().split()[2]
            for _ in range(30):
                if s.paused:
                    break
                time.sleep(0.2)
            self.assertTrue(s.paused)
            self.assertEqual(state(), "T")  # processus suspendu
            t.segment(s.sid, "10.m4s")   # le lecteur rattrape : reprise
            self.assertFalse(s.paused)
            time.sleep(2.5)
            self.assertFalse(os.path.exists(os.path.join(s.dir, "2.m4s")))  # vieux segments purgés
            t.stop(s.sid)
            self.assertIsNotNone(s.proc.poll())
            self.assertFalse(os.path.exists(s.dir))
        finally:
            tr.AHEAD_PAUSE, tr.AHEAD_RESUME, tr.KEEP_BEHIND = old
            t.stop_all()
            shutil.rmtree(work, ignore_errors=True)

    def test_subtitle_parser(self):
        vtt = b"WEBVTT\n\n00:01.000 --> 00:02.500 align:start\n<b>Salut</b>\n"
        self.assertEqual(parse_subtitles(vtt), [{"s": 1000, "e": 2500, "t": "Salut"}])
        self.assertEqual(parse_subtitles(b"\x00\xff garbage"), [])

    def test_names(self):
        from seance.names import parse_episode, parse_movie
        self.assertEqual(parse_movie("/m/Inception.2010.1080p.BluRay.x264-GRP.mkv", "/m"), ("Inception", 2010))
        e = parse_episode("/s/Frieren/[SubsPlease] Sousou no Frieren - 12 (1080p).mkv", "/s")
        self.assertEqual((e["show"], e["season"], e["episode"]), ("Frieren", 1, 12))
        e = parse_episode("/s/Les Revenants (2012)/Saison 2/Les Revenants - 2x03.mkv", "/s")
        self.assertEqual((e["show"], e["year"], e["season"], e["episode"]), ("Les Revenants", 2012, 2, 3))
        self.assertIsNone(parse_episode("/s/Show/bonus making-of.mkv", "/s"))


if __name__ == "__main__":
    unittest.main()
