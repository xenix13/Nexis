"""Trakt (faux serveur d'API) et Letterboxd (export / import de fichiers).

python3 -m unittest tests.test_tracking
"""
import csv
import io
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.test_api import STATIC, Client, media_root, wait_scan

from seance import tasks, tracking
from seance.api import Ctx, build_app
from seance.web import Server, make_handler

CID, SECRET = "c" * 64, "s" * 64


class FakeTrakt(BaseHTTPRequestHandler):
    posts = []
    polls = 0
    data = {}

    def log_message(self, *a):
        pass

    def reply(self, status, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("trakt-api-key") != CID:
            return self.reply(403)
        if self.headers.get("Authorization") != "Bearer TOKEN":
            return self.reply(401)
        if self.path == "/users/settings":
            return self.reply(200, {"user": {"username": "lea_trakt"}})
        key = self.path.split("?")[0]
        return self.reply(200, FakeTrakt.data.get(key, []))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/oauth/device/code":
            return self.reply(200, {"device_code": "DEV", "user_code": "AB12CD34", "verification_url": "https://trakt.tv/activate",
                                    "expires_in": 600, "interval": 1})
        if self.path == "/oauth/device/token":
            FakeTrakt.polls += 1
            if body.get("client_secret") != SECRET:
                return self.reply(401)
            if FakeTrakt.polls == 1:
                return self.reply(400)  # l'utilisateur n'a pas encore saisi le code
            return self.reply(200, {"access_token": "TOKEN", "refresh_token": "R", "expires_in": 7776000,
                                    "created_at": int(time.time())})
        if self.path == "/oauth/revoke":
            return self.reply(200, {})
        FakeTrakt.posts.append((self.path, body))
        # comme le vrai Trakt : ce qui est envoyé revient aux lectures suivantes
        if self.path == "/sync/history":
            FakeTrakt.data.setdefault("/sync/watched/movies", []).extend(
                {"movie": {"ids": m["ids"]}, "last_watched_at": m["watched_at"]} for m in body.get("movies", []))
        if self.path == "/sync/ratings":
            FakeTrakt.data.setdefault("/sync/ratings/movies", []).extend(
                {"movie": {"ids": m["ids"]}, "rating": m["rating"]} for m in body.get("movies", []))
        return self.reply(201, {"added": {}})


class TrackingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeTrakt)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.saved = tracking.API
        tracking.API = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        cls.work = tempfile.mkdtemp()
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.srv = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        cls.admin.call("POST", "/api/admin/libraries", {"name": "Films", "kind": "movie",
                                                       "paths": [os.path.join(media_root(), "films")]})
        wait_scan(cls.admin)
        lib = cls.admin.call("GET", "/api/libraries")[1][0]["id"]
        cls.movies = {}
        for n, m in enumerate(sorted(cls.admin.call("GET", f"/api/libraries/{lib}/items")[1]["items"], key=lambda m: m["title"]), 1):
            cls.ctx.db.run("UPDATE items SET tmdb_id=?, year=? WHERE id=?", (1000 + n, 2000 + n, int(m["id"])))
            cls.movies[m["title"]] = dict(m, tmdb=1000 + n, year=2000 + n)
        cls.admin.call("POST", "/api/admin/users", {"username": "lea", "password": "motdepasse2"})
        cls.lea = Client(cls.base)
        cls.lea.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})

    @classmethod
    def tearDownClass(cls):
        tracking.API = cls.saved
        cls.srv.shutdown()
        cls.fake.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def m(self, part):
        return next(v for k, v in self.movies.items() if part in k)

    def watched(self, client, part):
        return client.call("GET", f"/api/items/{self.m(part)['id']}")[1].get("watched")

    def test_01_trakt(self):
        st, v = self.lea.call("GET", "/api/me/trakt")
        self.assertEqual((v["configured"], v["linked"]), (False, False))
        self.assertNotIn("client_id", v)  # réglage du serveur : administrateurs seulement
        self.assertEqual(self.lea.call("PUT", "/api/admin/trakt", {"client_id": CID})[0], 403)
        self.assertEqual(self.admin.call("PUT", "/api/admin/trakt", {"client_id": "court"})[0], 400)
        st, v = self.admin.call("PUT", "/api/admin/trakt", {"client_id": CID, "client_secret": SECRET})
        self.assertEqual((st, v["configured"], v["secret_set"]), (200, True, True))
        self.assertNotIn(SECRET, json.dumps(v))
        brume, voyage, polaire = self.m("Brume"), self.m("Voyage"), self.m("olaire")
        FakeTrakt.data = {
            "/sync/watched/movies": [{"movie": {"ids": {"tmdb": brume["tmdb"]}}, "last_watched_at": "2024-03-01T20:00:00.000Z"},
                                     {"movie": {"ids": {"tmdb": 99999}}, "last_watched_at": "2024-03-02T20:00:00.000Z"}],
            "/sync/ratings/movies": [{"movie": {"ids": {"tmdb": voyage["tmdb"]}}, "rating": 8}],
            "/sync/watchlist/movies": [{"movie": {"ids": {"tmdb": polaire["tmdb"]}}}],
        }
        # vu sur Nexis avant de relier : doit partir vers Trakt
        self.lea.call("POST", f"/api/items/{voyage['id']}/watched", {"watched": True})
        st, c = self.lea.call("POST", "/api/me/trakt/link")
        self.assertEqual((st, c["user_code"]), (200, "AB12CD34"))
        self.assertEqual(self.lea.call("POST", "/api/me/trakt/poll")[1]["status"], "pending")
        self.assertEqual(self.lea.call("POST", "/api/me/trakt/poll")[1]["status"], "linked")
        deadline = time.time() + 20
        while time.time() < deadline and not (self.lea.call("GET", "/api/me/trakt")[1].get("last_sync")):
            time.sleep(0.2)
        st, v = self.lea.call("GET", "/api/me/trakt")
        self.assertEqual((v["linked"], v["username"], v["last_error"]), (True, "lea_trakt", None), v)
        self.assertTrue(self.watched(self.lea, "Brume"))
        hist = self.ctx.db.one("SELECT watched_at FROM history h JOIN users u ON u.id=h.user_id WHERE u.username='lea' "
                               "AND rating_key=?", (brume["id"],))
        self.assertEqual(time.strftime("%Y-%m-%d", time.gmtime(hist["watched_at"])), "2024-03-01")  # date de Trakt
        st, d = self.lea.call("GET", f"/api/items/{voyage['id']}")
        self.assertEqual(d["stars"], 4)  # 8/10 -> 4 étoiles
        st, wl = self.lea.call("GET", "/api/watchlist")
        self.assertIn(polaire["id"], json.dumps(wl))
        sent = dict(FakeTrakt.posts)
        self.assertEqual([m["ids"]["tmdb"] for m in sent["/sync/history"]["movies"]], [voyage["tmdb"]])
        self.assertNotIn("/sync/ratings", sent)  # Nexis n'avait aucune note à envoyer
        # une note donnée sur Nexis part au passage suivant (tâche planifiée)
        self.lea.call("PUT", f"/api/items/{polaire['id']}/stars", {"stars": 5})
        FakeTrakt.posts.clear()
        tm = self.ctx.tasks
        tm._execute(tasks.Job(tm.tasks["trakt"], "test"))
        self.assertEqual(tm.last("trakt")["status"], "ok", tm.last("trakt"))
        sent = dict(FakeTrakt.posts)
        self.assertEqual(sent["/sync/ratings"]["movies"], [{"ids": {"tmdb": polaire["tmdb"]}, "rating": 10}])
        self.assertNotIn("/sync/history", sent)  # rien de nouveau
        # délier
        st, v = self.lea.call("DELETE", "/api/me/trakt")
        self.assertFalse(v["linked"])

    def test_02_letterboxd(self):
        u = Client(self.base)
        self.admin.call("POST", "/api/admin/users", {"username": "max", "password": "motdepasse3"})
        u.call("POST", "/api/login", {"username": "max", "password": "motdepasse3"})
        brume, voyage, polaire = self.m("Brume"), self.m("Voyage"), self.m("olaire")
        u.call("POST", f"/api/items/{brume['id']}/watched", {"watched": True})
        u.call("PUT", f"/api/items/{brume['id']}/stars", {"stars": 3})
        st, data, h = u.call("GET", "/api/me/letterboxd.csv", raw=True)
        self.assertEqual(st, 200)
        rows = list(csv.DictReader(io.StringIO(data.decode())))
        self.assertEqual([(r["tmdbID"], r["Title"], r["Rating"]) for r in rows], [(str(brume["tmdb"]), brume["title"], "3")])
        # export de Letterboxd : un film vu (titre et année), une note, la liste « À regarder », un film absent
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("watched.csv", "Date,Name,Year,Letterboxd URI\n2023-05-02,%s,%d,https://boxd.it/a\n"
                       "2023-05-03,Film Introuvable,1999,https://boxd.it/b\n" % (voyage["title"], voyage["year"]))
            f.writestr("ratings.csv", "Date,Name,Year,Letterboxd URI,Rating\n2023-05-02,%s,%d,https://boxd.it/a,4.5\n"
                       % (voyage["title"], voyage["year"]))
            f.writestr("watchlist.csv", "Date,Name,Year,Letterboxd URI\n2023-01-01,%s,%d,https://boxd.it/c\n"
                       % (polaire["title"].upper(), polaire["year"] + 1))  # casse et année à un an près
        st, r = u.call("POST", "/api/me/letterboxd", raw_body=z.getvalue(), headers={"Content-Type": "application/zip"})
        self.assertEqual(st, 200, r)
        self.assertEqual((r["watched"], r["rated"], r["watchlist"], r["unmatched"]), (1, 1, 1, ["Film Introuvable (1999)"]))
        self.assertTrue(self.watched(u, "Voyage"))
        self.assertEqual(u.call("GET", f"/api/items/{voyage['id']}")[1]["stars"], 4)  # 4,5 arrondi
        st, r = u.call("POST", "/api/me/letterboxd", raw_body=b"PK pas un zip")
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
