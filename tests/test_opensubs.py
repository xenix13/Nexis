"""Sous-titres OpenSubtitles : réglages, recherche, téléchargement, tâche automatique (faux serveur d'API).

python3 -m unittest tests.test_opensubs
"""
import json
import os
import shutil
import struct
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tests.test_api import STATIC, Client, media_root, wait_scan

from seance import opensubs, tasks
from seance.api import Ctx, build_app
from seance.web import Server, make_handler

SRT = "1\n00:00:01,000 --> 00:00:03,000\nBonjour {n}\n\n".encode()


class FakeOS(BaseHTTPRequestHandler):
    """Faux api.opensubtitles.com : un sous-titre français calé sur le fichier, un autre non, un anglais."""
    calls = []
    remaining = 5
    fail_login = False

    def log_message(self, *a):
        pass

    def reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        FakeOS.calls.append(("GET", u.path, parse_qs(u.query), dict(self.headers)))
        if u.path.startswith("/files/"):  # lien de téléchargement : sans clé
            body = SRT.replace(b"{n}", u.path.rsplit("/", 1)[1].encode())
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.headers.get("Api-Key") != "CLE123":
            return self.reply(401, {"message": "bad key"})
        if u.path == "/api/v1/infos/user":
            return self.reply(200, {"data": {"remaining_downloads": FakeOS.remaining, "allowed_downloads": 20, "level": "Sub leecher"}})
        if u.path == "/api/v1/subtitles":
            q = parse_qs(u.query)
            langs = q.get("languages", [""])[0].split(",")
            data = []
            base = int(q.get("tmdb_id", ["7"])[0]) * 100
            if "fr" in langs:
                data += [
                    {"attributes": {"language": "fr", "release": "Autre.Version", "download_count": 900, "files": [{"file_id": base + 1}]}},
                    {"attributes": {"language": "fr", "release": "Exact.Release", "download_count": 10, "moviehash_match": True,
                                    "files": [{"file_id": base + 2}]}},
                    {"attributes": {"language": "fr", "release": "Machine", "download_count": 5000, "machine_translated": True,
                                    "files": [{"file_id": base + 3}]}},
                ]
            if "en" in langs:
                data.append({"attributes": {"language": "en", "release": "English", "hearing_impaired": True,
                                            "files": [{"file_id": base + 4}]}})
            return self.reply(200, {"data": data})
        self.reply(404, {"message": "?"})

    def do_POST(self):
        u = urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FakeOS.calls.append(("POST", u.path, body, dict(self.headers)))
        if u.path == "/api/v1/login":
            if FakeOS.fail_login or body != {"username": "lea", "password": "secret"}:
                return self.reply(401, {"message": "Error, invalid username/password"})
            return self.reply(200, {"token": "TOK", "user": {"allowed_downloads": 20}, "base_url": "api.opensubtitles.com"})
        if u.path == "/api/v1/download":
            if self.headers.get("Authorization") != "Bearer TOK":
                return self.reply(401, {"message": "login"})
            if FakeOS.remaining <= 0:
                return self.reply(406, {"message": "You have downloaded your allowed 20 subtitles"})
            FakeOS.remaining -= 1
            host = f"http://127.0.0.1:{self.server.server_address[1]}"
            return self.reply(200, {"link": f"{host}/files/{body['file_id']}", "remaining": FakeOS.remaining,
                                    "reset_time_utc": "2099-01-01T00:00:00.000Z"})
        self.reply(404, {"message": "?"})


class OpenSubtitlesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeOS)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.saved_api = opensubs.API
        opensubs.API = f"http://127.0.0.1:{cls.fake.server_address[1]}/api/v1"
        cls.work = tempfile.mkdtemp()
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.srv = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        st, r = cls.admin.call("POST", "/api/admin/libraries", {"name": "Films", "kind": "movie",
                                                                "paths": [os.path.join(media_root(), "films")]})
        assert st == 200, r
        wait_scan(cls.admin)
        lib = cls.admin.call("GET", "/api/libraries")[1][0]["id"]
        cls.movies = {m["title"]: m for m in cls.admin.call("GET", f"/api/libraries/{lib}/items")[1]["items"]}
        # identifiants TMDB fixés : le faux serveur répond selon eux
        for n, title in enumerate(sorted(cls.movies), 1):
            cls.ctx.db.run("UPDATE items SET tmdb_id=? WHERE id=?", (n, int(cls.movies[title]["id"])))

    @classmethod
    def tearDownClass(cls):
        opensubs.API = cls.saved_api
        cls.srv.shutdown()
        cls.fake.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_01_moviehash(self):
        p = os.path.join(self.work, "h.bin")
        data = bytes(range(256)) * 1024  # 256 Ko
        with open(p, "wb") as f:
            f.write(data)
        h = len(data)
        for chunk in (data[:65536], data[-65536:]):
            for (v,) in struct.iter_unpack("<Q", chunk):
                h = (h + v) & 0xFFFFFFFFFFFFFFFF
        self.assertEqual(opensubs.moviehash(p), f"{h:016x}")
        with open(p, "wb") as f:
            f.write(b"x" * 1000)
        self.assertIsNone(opensubs.moviehash(p))  # trop petit

    def test_02_settings(self):
        self.assertFalse(self.admin.call("GET", "/api/me")[1]["features"]["opensubtitles"])
        st, r = self.admin.call("PUT", "/api/admin/opensubtitles", {"key": "CLE123", "user": "lea", "password": "faux"})
        self.assertEqual(st, 400)
        self.assertIn("refuse", r["error"])
        self.assertEqual(self.admin.call("PUT", "/api/admin/opensubtitles", {"langs": ["fra", "xxx"]})[0], 400)
        self.assertEqual(self.admin.call("PUT", "/api/admin/opensubtitles", {"key": "clé avec espaces"})[0], 400)
        st, r = self.admin.call("PUT", "/api/admin/opensubtitles", {"key": "CLE123", "user": "lea", "password": "secret",
                                                                     "langs": ["fra", "eng"]})
        self.assertEqual(st, 200, r)
        self.assertEqual((r["account"]["remaining"], r["langs"], r["password_set"]), (5, ["fra", "eng"], True))
        self.assertNotIn("password", json.dumps(r).replace("password_set", ""))  # le mot de passe ne revient jamais
        self.assertTrue(self.admin.call("GET", "/api/me")[1]["features"]["opensubtitles"])
        # mot de passe vide : gardé
        st, r = self.admin.call("PUT", "/api/admin/opensubtitles", {"user": "lea", "password": ""})
        self.assertEqual((st, r["password_set"]), (200, True))
        self.assertEqual(Client(self.base).call("GET", "/api/admin/opensubtitles")[0], 401)

    def test_03_search_and_download(self):
        m = self.movies["Brume"]
        st, r = self.admin.call("GET", f"/api/items/{m['id']}/opensubtitles?langs=fra")
        self.assertEqual(st, 200, r)
        self.assertEqual([x["release"] for x in r["results"]][:2], ["Exact.Release", "Autre.Version"])  # calé sur le fichier d'abord
        self.assertTrue(r["results"][-1]["machine"])
        q = next(c for c in FakeOS.calls if c[1] == "/api/v1/subtitles")[2]
        self.assertIn("moviehash", q)
        self.assertEqual(q["languages"], ["fr"])
        exact = r["results"][0]
        st, r = self.admin.call("POST", f"/api/items/{m['id']}/opensubtitles", {"file_id": exact["file_id"], "lang": "fra",
                                                                               "release": exact["release"]})
        self.assertEqual(st, 200, r)
        sub = next(s for s in r["subtitles"] if s["external"] and s["lang"] == "fra")
        st, cues = self.admin.call("GET", f"/api/items/{m['id']}/subtitles/{sub['id']}")
        self.assertEqual(cues["cues"][0]["t"], f"Bonjour {exact['file_id']}")
        self.assertEqual(self.admin.call("POST", f"/api/items/{m['id']}/opensubtitles", {"file_id": "x", "lang": "fra"})[0], 400)
        # un nouveau scan garde le sous-titre téléchargé
        self.admin.call("POST", "/api/admin/scan", {})
        wait_scan(self.admin)
        st, d = self.admin.call("GET", f"/api/items/{m['id']}")
        self.assertTrue(any(s["lang"] == "fra" and s["external"] for s in d["subtitles"]))

    def run_task(self):
        tm = self.ctx.tasks
        tm._execute(tasks.Job(tm.tasks["opensubs"], "test"))

    def test_04_task_downloads_missing(self):
        FakeOS.remaining = 5
        self.run_task()
        last = self.ctx.tasks.last("opensubs")
        self.assertEqual(last["status"], "ok", last)
        # Nuit polaire : français intégré au fichier, anglais à côté -> rien à télécharger
        polar = next(v for k, v in self.movies.items() if "olaire" in k)
        probe = json.loads(self.ctx.db.one("SELECT probe FROM items WHERE id=?", (int(polar["id"]),))["probe"])
        self.assertFalse([e for e in probe["external"] if e.get("dl")])
        self.assertEqual(last["summary"], "3 sous-titre(s) téléchargé(s) pour 2 titre(s)")
        voyage = next(v for k, v in self.movies.items() if "Voyage" in k)
        st, d = self.admin.call("GET", f"/api/items/{voyage['id']}")
        dl = [s for s in d["subtitles"] if s["external"]]
        self.assertEqual(sorted(s["lang"] for s in dl), ["eng", "fra"])  # SDH accepté faute d'autre anglais
        # retiré à la main : pas retéléchargé
        probe = json.loads(self.ctx.db.one("SELECT probe FROM items WHERE id=?", (int(voyage["id"]),))["probe"])
        fid = next(e["file_id"] for e in probe["external"] if e.get("dl") and e["lang"] == "fra")
        self.assertEqual(self.admin.call("DELETE", f"/api/items/{voyage['id']}/opensubtitles/{fid}")[0], 200)
        before = FakeOS.remaining
        self.run_task()
        self.assertEqual(FakeOS.remaining, before)
        st, d = self.admin.call("GET", f"/api/items/{voyage['id']}")
        self.assertNotIn("fra", [s["lang"] for s in d["subtitles"] if s["external"]])

    def test_05_quota(self):
        FakeOS.remaining = 0
        self.ctx.db.set_setting("os_remaining", 0)
        m = self.movies["Brume"]
        st, r = self.admin.call("POST", f"/api/items/{m['id']}/opensubtitles", {"file_id": 701, "lang": "fra"})
        self.assertEqual(st, 502)
        self.assertIn("Quota", r["error"])


if __name__ == "__main__":
    unittest.main()
