"""End-to-end API tests against a fake Plex server. Run: python3 -m unittest discover -s tests"""
import http.cookiejar
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from seance.api import Ctx, build_app  # noqa: E402
from seance.subtitles import parse_subtitles  # noqa: E402
from seance.web import Server, make_handler  # noqa: E402
from tests.fakeplex import TOKEN, FakePlex  # noqa: E402

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


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
            with self.opener.open(req, timeout=30) as r:
                content = r.read()
                status, hdrs = r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            content, status, hdrs = e.read(), e.code, dict(e.headers)
        if raw:
            return status, content, hdrs
        try:
            return status, json.loads(content)
        except json.JSONDecodeError:
            return status, content


class SeanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plex = FakePlex()
        cls.tmp = tempfile.mkdtemp()
        cls.ctx = Ctx(cls.tmp, STATIC)
        cls.server = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.admin = Client(cls.base)
        code = cls.ctx.setup_code
        st, r = cls.admin.call("POST", "/api/setup", {"username": "colas", "password": "motdepasse1",
                                                      "plex_url": cls.plex.url, "plex_token": TOKEN},
                               headers={"X-Setup-Code": code})
        assert st == 200, r
        st, u = cls.admin.call("POST", "/api/admin/users", {
            "username": "lea", "password": "motdepasse2", "display_name": "Léa",
            "all_libraries": False, "libraries": ["1", "3"]})
        assert st == 200, u
        cls.lea_id = u["id"]
        cls.lea = Client(cls.base)
        st, r = cls.lea.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})
        assert st == 200, r

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.plex.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- setup/auth
    def test_setup_is_locked_after_first_admin(self):
        c = Client(self.base)
        st, r = c.call("POST", "/api/setup", {"username": "evil", "password": "xxxxxxxxxx",
                                              "plex_url": self.plex.url, "plex_token": TOKEN},
                       headers={"X-Setup-Code": "WHATEVER"})
        self.assertEqual(st, 403)
        st, r = c.call("GET", "/api/status")
        self.assertFalse(r["setup"])

    def test_requires_login_and_csrf_header(self):
        c = Client(self.base)
        self.assertEqual(c.call("GET", "/api/home")[0], 401)
        st, r = c.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"}, csrf=False)
        self.assertEqual(st, 403)

    def test_wrong_password_and_throttle(self):
        c = Client(self.base)
        codes = [c.call("POST", "/api/login", {"username": "bob", "password": "nope"})[0] for _ in range(9)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8], 429)
        self.ctx.throttle.fails.clear()

    def test_non_admin_cannot_use_admin(self):
        self.assertEqual(self.lea.call("GET", "/api/admin/users")[0], 403)
        self.assertEqual(self.lea.call("PUT", "/api/admin/settings", {"plex_url": "http://x"})[0], 403)

    def test_token_never_exposed(self):
        for path in ("/api/me", "/api/home", "/api/admin/settings", "/api/items/3111",
                     "/api/libraries/1/items"):
            st, body, _ = self.admin.call("GET", path, raw=True)
            self.assertEqual(st, 200, path)
            self.assertNotIn(TOKEN.encode(), body, path)

    # ------------------------------------------------------------- libraries
    def test_library_permissions(self):
        st, me = self.lea.call("GET", "/api/me")
        self.assertEqual([l["id"] for l in me["libraries"]], ["1", "3"])  # music hidden, series not granted
        self.assertEqual(self.lea.call("GET", "/api/libraries/2/items")[0], 404)
        self.assertEqual(self.lea.call("GET", "/api/items/201")[0], 404)
        self.assertEqual(self.lea.call("GET", "/api/items/2111")[0], 404)
        self.assertEqual(self.lea.call("POST", "/api/play", {"id": "2111"})[0], 404)
        st, s = self.lea.call("GET", "/api/search?q=les")
        self.assertEqual(st, 200)
        self.assertFalse(any(x["id"] == "201" for x in s["shows"]))
        self.assertTrue(any(x["id"] == "103" for x in s["movies"]))  # "Les Horizons"
        st, _, _ = self.lea.call("GET", "/api/img?p=/library/metadata/201/thumb/1700000000&w=200&h=300", raw=True)
        self.assertEqual(st, 404)

    def test_library_listing_and_genres(self):
        st, r = self.admin.call("GET", "/api/libraries/1/items?sort=added&size=4")
        self.assertEqual(st, 200)
        self.assertEqual(r["total"], 6)
        self.assertEqual(len(r["items"]), 4)
        self.assertEqual(r["items"][0]["title"], "Station Zéro")
        st, g = self.admin.call("GET", "/api/libraries/1/genres")
        self.assertEqual([x["id"] for x in g], ["11", "12"])
        self.assertEqual(self.admin.call("GET", "/api/libraries/1/items?genre=1;DROP")[0], 400)

    def test_image_proxy_and_validation(self):
        st, body, h = self.admin.call("GET", "/api/img?p=/library/metadata/101/thumb/1700000000&w=200&h=300",
                                      raw=True)
        self.assertEqual(st, 200)
        self.assertTrue(h["Content-Type"].startswith("image/"))
        self.assertTrue(body[:2] == b"\xff\xd8")
        for bad in ("http://evil/x.jpg", "/library/metadata/101/../../x", "/:/prefs", "/library/parts/1/file.mkv"):
            st, _, _ = self.admin.call("GET", "/api/img?p=" + urllib.request.quote(bad), raw=True)
            self.assertEqual(st, 400, bad)

    def test_show_detail_and_next_target(self):
        st, d = self.admin.call("GET", "/api/items/201")
        self.assertEqual(st, 200)
        self.assertEqual([s["index"] for s in d["seasons"]], [1, 2])
        self.assertEqual(d["play_target"]["id"], "2111")
        st, eps = self.admin.call("GET", "/api/items/211/children")
        self.assertEqual([e["episode"] for e in eps], [1, 2, 3])

    # -------------------------------------------------------------- playback
    def test_play_hls_proxy_and_progress(self):
        c = Client(self.base)
        c.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})
        st, p = c.call("POST", "/api/play", {"id": "3111", "offset": 15000, "audio": 3111003,
                                              "subtitle": 3111004})
        self.assertEqual(st, 200, p)
        self.assertEqual(p["subtitle_overlay"], "/api/items/3111/subtitles/3111004")
        self.assertEqual(p["markers"]["intro"]["start"], 30000)
        self.assertEqual(p["next"]["id"], "3112")
        put = self.plex.puts[-1]
        self.assertEqual(put[1]["audioStreamID"], "3111003")
        self.assertEqual(put[1]["subtitleStreamID"], "0")  # overlay sub => nothing burned

        st, master, h = c.call("GET", p["url"], raw=True)
        self.assertEqual(st, 200)
        self.assertNotIn(TOKEN.encode(), master)
        start_req = [r for r in self.plex.requests if r[1].endswith("start.m3u8")][-1]
        self.assertEqual(start_req[2]["offset"], "15.0")
        self.assertEqual(start_req[2]["protocol"], "hls")
        variant = [l for l in master.decode().splitlines() if l and not l.startswith("#")][0]
        base = p["url"].rsplit("/", 1)[0] + "/"
        st, index, _ = c.call("GET", base + variant, raw=True)
        self.assertEqual(st, 200)
        seg = [l for l in index.decode().splitlines() if l and not l.startswith("#")][0]
        var_dir = (base + variant).split("?")[0].rsplit("/", 1)[0] + "/"
        st, ts, h = c.call("GET", var_dir + seg, raw=True)
        self.assertEqual(st, 200)
        self.assertEqual(ts[0], 0x47)

        # another user cannot use this stream; path traversal refused
        self.assertEqual(self.admin.call("GET", p["url"], raw=True)[0], 404)
        self.assertEqual(c.call("GET", base + "session/../../../library/sections", raw=True)[0], 404)
        self.assertEqual(c.call("GET", base + "../library", raw=True)[0], 404)

        # overlay subtitles
        st, subs = c.call("GET", p["subtitle_overlay"])
        self.assertEqual(subs["cues"][0], {"s": 1000, "e": 4000, "t": "Bonjour tout le monde"})

        # progress -> continue row
        st, r = c.call("POST", "/api/progress", {"id": "3111", "position": 600000, "duration": 1440000,
                                                  "stream": p["stream"], "state": "playing"})
        self.assertEqual(st, 200)
        self.assertFalse(r["watched"])
        st, home = c.call("GET", "/api/home")
        self.assertEqual(home["continue"][0]["id"], "3111")
        self.assertEqual(home["continue"][0]["position"], 600000)
        self.assertEqual({r["library"]["id"] for r in home["rows"]}, {"1", "3"})

        # finishing -> watched, history, next up
        st, r = c.call("POST", "/api/progress", {"id": "3111", "position": 1400000, "duration": 1440000,
                                                  "stream": p["stream"], "state": "stopped"})
        self.assertTrue(r["watched"])
        for _ in range(50):  # the transcoder stop is sent asynchronously
            if start_req[2]["session"] in self.plex.stopped:
                break
            time.sleep(0.05)
        self.assertIn(start_req[2]["session"], self.plex.stopped)
        st, home = c.call("GET", "/api/home")
        self.assertEqual(home["continue"][0]["id"], "3112")
        self.assertTrue(home["continue"][0]["next_up"])
        st, hist = c.call("GET", "/api/history")
        self.assertEqual(hist["items"][0]["id"], "3111")
        st, d = c.call("GET", "/api/items/301")
        self.assertEqual(d["watched_count"], 1)
        self.assertEqual(d["play_target"]["id"], "3112")

        # watched state is per user
        st, d = self.admin.call("GET", "/api/items/3111")
        self.assertFalse(d["watched"])

    def test_burned_subtitles_and_quality(self):
        st, p = self.admin.call("POST", "/api/play", {"id": "3112", "subtitle": 3112005, "quality": "720p"})
        self.assertEqual(st, 200, p)
        self.assertIsNone(p["subtitle_overlay"])
        self.assertEqual(self.plex.puts[-1][1]["subtitleStreamID"], "3112005")
        self.admin.call("GET", p["url"], raw=True)
        start_req = [r for r in self.plex.requests if r[1].endswith("start.m3u8")][-1]
        self.assertEqual(start_req[2]["maxVideoBitrate"], "4000")
        self.assertEqual(self.admin.call("POST", "/api/play", {"id": "3112", "subtitle": 999})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/play", {"id": "3112", "quality": "8k"})[0], 400)
        # a seek restarts the stream: the old transcode is stopped before the new one starts
        old_session = start_req[2]["session"]
        st, p2 = self.admin.call("POST", "/api/play", {"id": "3112", "offset": 600000, "replace": p["stream"]})
        self.assertEqual(st, 200)
        self.assertIn(old_session, self.plex.stopped)
        self.assertEqual(self.admin.call("GET", p["url"], raw=True)[0], 404)
        self.admin.call("POST", "/api/stop", {"stream": p2["stream"]})

    def test_mark_show_watched(self):
        st, r = self.admin.call("POST", "/api/items/211/watched", {"watched": True})
        self.assertEqual(r["count"], 3)
        st, d = self.admin.call("GET", "/api/items/201")
        self.assertEqual(d["watched_count"], 3)
        self.assertEqual(d["play_target"]["id"], "2121")
        self.admin.call("POST", "/api/items/201/watched", {"watched": False})
        st, d = self.admin.call("GET", "/api/items/201")
        self.assertEqual(d["watched_count"], 0)

    # ------------------------------------------------------------------ prefs/admin
    def test_prefs_validation(self):
        st, p = self.lea.call("PUT", "/api/me/prefs", {"accent": "#ff0055", "sub_mode": "always"})
        self.assertEqual(st, 200)
        self.assertEqual(p["accent"], "#ff0055")
        self.assertEqual(self.lea.call("PUT", "/api/me/prefs", {"accent": "red;}"})[0], 400)
        self.assertEqual(self.lea.call("PUT", "/api/me/prefs", {"sub_size": 5000})[0], 400)
        st, me = self.lea.call("GET", "/api/me")
        self.assertEqual(me["prefs"]["sub_mode"], "always")
        st, me2 = self.admin.call("GET", "/api/me")
        self.assertEqual(me2["prefs"]["sub_mode"], "foreign")

    def test_admin_safeguards(self):
        st, users = self.admin.call("GET", "/api/admin/users")
        me = [u for u in users if u["username"] == "colas"][0]
        self.assertEqual(self.admin.call("DELETE", f"/api/admin/users/{me['id']}")[0], 400)
        self.assertEqual(self.admin.call("PUT", f"/api/admin/users/{me['id']}", {"is_admin": False})[0], 400)
        st, u = self.admin.call("POST", "/api/admin/users", {"username": "temp", "password": "motdepasse3"})
        self.assertEqual(st, 200)
        t = Client(self.base)
        self.assertEqual(t.call("POST", "/api/login", {"username": "temp", "password": "motdepasse3"})[0], 200)
        self.admin.call("PUT", f"/api/admin/users/{u['id']}", {"disabled": True})
        self.assertEqual(t.call("GET", "/api/me")[0], 401)
        self.assertEqual(self.admin.call("DELETE", f"/api/admin/users/{u['id']}")[0], 200)
        self.assertEqual(self.admin.call("POST", "/api/admin/users",
                                         {"username": "lea", "password": "motdepasse9"})[0], 409)

    def test_manual_markers(self):
        self.assertEqual(self.admin.call("PUT", "/api/admin/markers/2111",
                                         {"kind": "intro", "start": 5000, "end": 65000})[0], 200)
        st, d = self.admin.call("GET", "/api/items/2111")
        self.assertEqual(d["markers"]["intro"], {"start": 5000, "end": 65000, "source": "manual"})

    def test_static_and_spa(self):
        st, body, h = self.admin.call("GET", "/", raw=True)
        self.assertEqual(st, 200)
        self.assertIn(b"<!doctype html>", body.lower())
        self.assertIn("Content-Security-Policy", h)
        self.assertEqual(self.admin.call("GET", "/../seance/api.py", raw=True)[0], 404)
        self.assertEqual(self.admin.call("GET", "/js/nope.js", raw=True)[0], 404)


class SubtitleParserTest(unittest.TestCase):
    def test_vtt_and_bad_input(self):
        vtt = b"WEBVTT\n\n00:01.000 --> 00:02.500 align:start\n<b>Salut</b>\n\nbroken --> nope\nx\n"
        self.assertEqual(parse_subtitles(vtt), [{"s": 1000, "e": 2500, "t": "Salut"}])
        self.assertEqual(parse_subtitles(b"\x00\xff garbage"), [])


if __name__ == "__main__":
    unittest.main()
