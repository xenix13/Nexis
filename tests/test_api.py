"""Tests de bout en bout : vrais fichiers vidéo (ffmpeg), faux TMDB.

python3 -m unittest discover -s tests -t .
"""
import http.cookiejar
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.fixtures import FakeTMDB, make_media  # noqa: E402

TMDB = FakeTMDB()
os.environ["SEANCE_TMDB_API"] = TMDB.url
os.environ["SEANCE_TMDB_IMG"] = TMDB.url + "/img"

from seance.api import Ctx, build_app  # noqa: E402
from seance.subtitles import parse_subtitles  # noqa: E402
from seance.transcoder import plan  # noqa: E402
from seance.web import Server, make_handler  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
MEDIA_CACHE = os.path.join(tempfile.gettempdir(), "seance-test-media-v2")


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
        self.assertIn("H.264 10 bits", r["reasons"])
        p["video"].update(profile="High", pix_fmt="yuv420p")
        self.assertEqual(plan(p)["mode"], "copy")
        self.assertEqual(plan(p, quality="720p")["mode"], "transcode")
        # sous-titres image (PGS) : incrustation par overlay, donc transcodage
        from seance.transcoder import build_command
        p["subs"] = [{"index": 2, "codec": "hdmv_pgs_subtitle"}]
        r = plan(p, burn_index=2)
        self.assertEqual(r["mode"], "transcode")
        self.assertIn("sous-titres incrustés", r["reasons"])
        cmd = build_command("/x.mkv", r, 12.5, "/tmp/out")
        self.assertIn("[0:0][0:2]overlay=eof_action=pass[o];[o]format=yuv420p[v]", cmd)
        self.assertEqual(cmd[cmd.index("-ss") + 1], "12.500")
        # un MP4 H.264/AAC est lu directement, sauf si on choisit la 2e piste audio
        mp4 = {"container": "mov,mp4,m4a,3gp,3g2,mj2", "bitrate": 0,
               "video": {"index": 0, "codec": "h264", "profile": "High", "pix_fmt": "yuv420p", "height": 720},
               "audio": [{"index": 1, "codec": "aac", "channels": 2}, {"index": 2, "codec": "aac", "channels": 2}]}
        self.assertEqual(plan(mp4)["mode"], "direct")
        self.assertEqual(plan(mp4, audio_index=2)["mode"], "copy")

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
        self.assertTrue(38000 <= p["offset"] <= 41000, p["offset"])  # calé sur une image clé (GOP 2 s)
        st, pl, _ = self.admin.call("GET", p["url"], raw=True)
        self.assertEqual(st, 200)
        seg = [l for l in pl.decode().splitlines() if l and not l.startswith("#")][0]
        st, ts, h = self.admin.call("GET", p["url"].rsplit("/", 1)[0] + "/" + seg, raw=True)
        self.assertEqual(st, 200)
        self.assertEqual(ffprobe_codec(ts), "h264")
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
        self.assertIn("vidéo HEVC", p["reasons"])
        st, p2 = self.admin.call("POST", "/api/play", {"id": m["id"]})
        self.assertEqual(st, 503)
        self.assertIn("transcodages simultanés", p2["error"])
        st, pl, _ = self.admin.call("GET", p["url"], raw=True)
        self.assertEqual(st, 200)
        seg = [l for l in pl.decode().splitlines() if l and not l.startswith("#")][0]
        st, ts, _ = self.admin.call("GET", p["url"].rsplit("/", 1)[0] + "/" + seg, raw=True)
        self.assertEqual(ffprobe_codec(ts), "h264")
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})
        self.admin.call("PUT", "/api/admin/settings", {"max_transcodes": 2})

    def test_subtitles_overlay_and_burn(self):
        show, d, eps = self.episodes("Kaze no Michi", "Animes")
        st, e1 = self.admin.call("GET", f"/api/items/{eps[0]['id']}")
        ass = e1["subtitles"][0]
        self.assertTrue(ass["overlay"])
        st, p = self.admin.call("POST", "/api/play", {"id": e1["id"], "subtitle": ass["id"]})
        self.assertEqual(p["mode"], "copy")  # l'ASS est affiché par le navigateur : pas de transcodage
        st, cues = self.admin.call("GET", p["subtitle_overlay"])
        self.assertEqual(cues["cues"][0]["t"], "Le vent se lève\nsur la montagne")
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})
        st, e2 = self.admin.call("GET", f"/api/items/{eps[1]['id']}")
        self.assertTrue(e2["subtitles"][0]["overlay"])
        # sous-titres internes SRT et externes
        m = self.movie("Nuit polaire")
        st, d = self.admin.call("GET", f"/api/items/{m['id']}")
        st, c = self.admin.call("GET", f"/api/items/{m['id']}/subtitles/{d['subtitles'][0]['id']}")
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
        self.assertEqual(self.lea.call("GET", "/api/admin/users")[0], 403)
        st, users = self.admin.call("GET", "/api/admin/users")
        me = [u for u in users if u["username"] == "colas"][0]
        self.assertEqual(self.admin.call("DELETE", f"/api/admin/users/{me['id']}")[0], 400)

    def test_static(self):
        st, body, h = self.admin.call("GET", "/", raw=True)
        self.assertIn(b"<!doctype html>", body.lower())
        self.assertEqual(self.admin.call("GET", "/../seance/api.py", raw=True)[0], 404)


class UnitTests(unittest.TestCase):
    def test_throttle_pauses_ffmpeg_ahead(self):
        from seance import transcoder as tr
        work = tempfile.mkdtemp()
        t = tr.Transcoder(os.path.join(work, "t"), lambda: (4, 2))
        old = (tr.AHEAD_PAUSE, tr.AHEAD_RESUME, tr.KEEP_BEHIND)
        tr.AHEAD_PAUSE, tr.AHEAD_RESUME, tr.KEEP_BEHIND = 5, 3, 2
        try:
            s = tr.Session("abcdefgh", "/x", {"mode": "copy"}, 0, os.path.join(work, "t", "abcdefgh"))
            os.makedirs(s.dir)
            for i in range(12):
                open(os.path.join(s.dir, f"s{i:05d}.ts"), "wb").close()
            s.proc = subprocess.Popen(["sleep", "30"])
            t.sessions[s.sid] = s
            state = lambda: open(f"/proc/{s.proc.pid}/stat").read().split()[2]
            for _ in range(30):
                if s.paused:
                    break
                time.sleep(0.2)
            self.assertTrue(s.paused)
            self.assertEqual(state(), "T")  # processus suspendu
            t.segment(s.sid, "s00010.ts")   # le lecteur rattrape : reprise
            self.assertFalse(s.paused)
            time.sleep(2.5)
            self.assertFalse(os.path.exists(os.path.join(s.dir, "s00002.ts")))  # vieux segments purgés
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
