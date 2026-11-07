"""Livres audio (bibliothèque) et podcasts (flux RSS relayés).

python3 -m unittest tests.test_books_podcasts
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.test_api import STATIC, Client, wait_scan

from seance import podcasts, tasks
from seance.api import Ctx, build_app
from seance.web import Server, make_handler

AUDIO = bytes(range(256)) * 400  # « fichier audio » de 100 Ko servi par le faux hébergeur


def tone(path, seconds, **tags):
    meta = [a for k, v in tags.items() for a in ("-metadata", f"{k}={v}")]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "libmp3lame", "-b:a", "32k", *meta, path], check=True)


class FakeHost(BaseHTTPRequestHandler):
    episodes = 2
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeHost.hits.append((self.path, self.headers.get("Range")))
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if self.path == "/feed.xml":
            items = "".join(
                f"<item><title>Épisode {n}</title><guid>ep-{n}</guid><pubDate>Mon, 0{n} Jan 2024 10:00:00 GMT</pubDate>"
                f"<itunes:duration>00:01:4{n}</itunes:duration><description>&lt;p&gt;Résumé {n}&lt;/p&gt;</description>"
                f"<enclosure url=\"{base}/audio/{n}.mp3\" type=\"audio/mpeg\" length=\"1\"/></item>"
                for n in range(FakeHost.episodes, 0, -1))
            body = (f'<?xml version="1.0"?><rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
                    f"<channel><title>Le Podcast</title><itunes:author>Radio Test</itunes:author>"
                    f'<itunes:image href="{base}/cover.png"/><description>Un podcast</description>{items}</channel></rss>').encode()
            return self.send(200, body, "application/rss+xml")
        if self.path == "/cover.png":
            return self.send(200, b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")
        if self.path.startswith("/audio/"):
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                part = AUDIO[start:]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(AUDIO) - 1}/{len(AUDIO)}")
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(part)))
                self.end_headers()
                self.wfile.write(part)
                return
            return self.send(200, AUDIO, "audio/mpeg")
        if self.path.startswith("/search"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            res = [{"collectionName": f"Résultat {q['term'][0]}", "artistName": "X", "feedUrl": f"{base}/feed.xml",
                    "artworkUrl600": f"{base}/cover.png"}]
            return self.send(200, json.dumps({"results": res}).encode(), "application/json")
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/interne")
            self.end_headers()
            return
        self.send(404, b"", "text/plain")

    def send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BooksPodcastsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        books = os.path.join(cls.work, "livres")
        d = os.path.join(books, "Jules Verne", "Le Tour du monde")
        os.makedirs(d)
        tone(os.path.join(d, "01 Départ.mp3"), 2, title="Départ", album="Le Tour du monde en 80 jours", artist="Jules Verne",
             composer="Lecteur A")
        tone(os.path.join(d, "02 Arrivée.mp3"), 3, title="Arrivée", album="Le Tour du monde en 80 jours", artist="Jules Verne")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=60x60", "-frames:v", "1",
                        os.path.join(d, "cover.jpg")], check=True)
        # un .m4b à la racine, avec ses chapitres
        meta = os.path.join(cls.work, "chap.txt")
        with open(meta, "w") as f:
            f.write(";FFMETADATA1\ntitle=Contes\nartist=Perrault\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=Le Chat\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=2000\nEND=4000\ntitle=La Belle\n")
        raw = os.path.join(cls.work, "raw.m4a")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=4", "-c:a", "aac", raw], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-i", meta, "-map", "0", "-map_metadata", "1",
                        "-map_chapters", "1", "-c", "copy", "-f", "mp4", os.path.join(books, "Contes.m4b")], check=True)
        old = time.time() - 3600
        for root, _, files in os.walk(books):
            for f in files:
                os.utime(os.path.join(root, f), (old, old))
        cls.host = ThreadingHTTPServer(("127.0.0.1", 0), FakeHost)
        threading.Thread(target=cls.host.serve_forever, daemon=True).start()
        cls.hbase = f"http://127.0.0.1:{cls.host.server_address[1]}"
        cls.saved = podcasts.SEARCH_API
        podcasts.SEARCH_API = cls.hbase + "/search"
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.srv = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        st, r = cls.admin.call("POST", "/api/admin/libraries", {"name": "Livres audio", "kind": "audiobook", "paths": [books]})
        assert st == 200, r
        wait_scan(cls.admin)
        cls.lib = next(l for l in cls.admin.call("GET", "/api/libraries")[1] if l["title"] == "Livres audio")
        cls.admin.call("POST", "/api/admin/users", {"username": "lea", "password": "motdepasse2"})
        cls.lea = Client(cls.base)
        cls.lea.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})
        cls.sent = []
        cls.ctx.push.notify = lambda uid, title, body, url="#/", tag="", kind=None: cls.sent.append((uid, title, body)) or 1

    @classmethod
    def tearDownClass(cls):
        podcasts.SEARCH_API = cls.saved
        cls.srv.shutdown()
        cls.host.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    # ---------------------------------------------------------------- livres audio
    def test_01_books_scanned(self):
        st, r = self.lea.call("GET", f"/api/libraries/{self.lib['id']}/books?sort=author")
        self.assertEqual(st, 200, r)
        titles = [(b["title"], b["author"]) for b in r["books"]]
        self.assertEqual(titles, [("Le Tour du monde en 80 jours", "Jules Verne"), ("Contes", "Perrault")])
        tour = r["books"][0]
        st, d = self.lea.call("GET", f"/api/books/{tour['id']}")
        self.assertEqual([c["t"] for c in d["chapter_list"]], ["Départ", "Arrivée"])
        self.assertEqual(d["chapter_list"][1]["s"], d["files"][0]["duration"])  # 2e fichier : à la suite du 1er
        self.assertEqual((d["narrator"], len(d["files"])), ("Lecteur A", 2))
        self.assertGreater(d["duration"], 4500)
        st, data, h = self.lea.call("GET", d["files"][1]["url"], headers={"Range": "bytes=0-99"}, raw=True)
        self.assertEqual((st, len(data), h["Content-Type"]), (206, 100, "audio/mpeg"))
        st, data, _ = self.lea.call("GET", f"/api/img?p={tour['thumb']}&w=100&h=100", raw=True)
        self.assertEqual((st, data[:2]), (200, b"\xff\xd8"))  # cover.jpg du dossier
        contes = r["books"][1]
        st, d = self.lea.call("GET", f"/api/books/{contes['id']}")
        self.assertEqual([c["t"] for c in d["chapter_list"]], ["Le Chat", "La Belle"])
        self.assertEqual(d["files"][0]["mime"], "audio/mp4")
        # les livres ne se mêlent pas aux films et séries
        st, home = self.lea.call("GET", "/api/home")
        self.assertNotIn("Contes", json.dumps(home))

    def test_02_book_progress(self):
        books = self.lea.call("GET", f"/api/libraries/{self.lib['id']}/books")[1]["books"]
        b = books[0]
        st, r = self.lea.call("POST", f"/api/books/{b['id']}/progress", {"position": 2500})
        self.assertEqual((st, r["finished"]), (200, False))
        st, r = self.lea.call("GET", f"/api/libraries/{self.lib['id']}/books")
        self.assertEqual([x["id"] for x in r["reading"]], [b["id"]])
        self.assertEqual(self.lea.call("GET", f"/api/books/{b['id']}")[1]["position"], 2500)
        st, r = self.lea.call("POST", f"/api/books/{b['id']}/progress", {"position": b["duration"]})
        self.assertTrue(r["finished"])
        self.assertEqual(self.lea.call("GET", f"/api/libraries/{self.lib['id']}/books")[1]["reading"], [])
        self.assertEqual(self.admin.call("GET", f"/api/books/{b['id']}")[1].get("position"), None)  # propre à chacun

    # ---------------------------------------------------------------- podcasts
    def test_10_podcasts(self):
        feed = self.hbase + "/feed.xml"
        st, r = self.lea.call("POST", "/api/podcasts", {"url": feed})
        self.assertEqual(st, 400)
        self.assertIn("réseau local", r["error"])  # un utilisateur ne fait pas viser le réseau du serveur
        st, p = self.admin.call("POST", "/api/podcasts", {"url": feed})
        self.assertEqual(st, 200, p)
        self.assertEqual((p["title"], p["author"], p["unplayed"]), ("Le Podcast", "Radio Test", 2))
        st, p2 = self.lea.call("POST", "/api/podcasts", {"url": feed})  # déjà connu du serveur
        self.assertEqual(st, 200, p2)
        st, d = self.lea.call("GET", f"/api/podcasts/{p['id']}")
        self.assertEqual([e["title"] for e in d["episodes"]], ["Épisode 2", "Épisode 1"])
        e = d["episodes"][0]
        self.assertEqual((e["duration"], e["description"]), (102000, "Résumé 2"))
        st, data, h = self.lea.call("GET", e["audio"], headers={"Range": "bytes=100-"}, raw=True)
        self.assertEqual((st, data, h["Content-Range"]), (206, AUDIO[100:], f"bytes 100-{len(AUDIO) - 1}/{len(AUDIO)}"))
        st, data, _ = self.lea.call("GET", p["image"], raw=True)
        self.assertEqual((st, data[:4]), (200, b"\x89PNG"))
        self.assertEqual(self.lea.call("GET", "/api/podcasts/image?u=" + urllib.parse.quote("http://exemple.org/x.png"))[0], 404)
        # écoute
        st, r = self.lea.call("POST", f"/api/podcasts/episodes/{e['id']}/progress", {"position": 30000, "duration": 102000})
        self.assertEqual((st, r["played"]), (200, False))
        st, mine = self.lea.call("GET", "/api/podcasts")
        self.assertEqual([x["id"] for x in mine["resume"]], [e["id"]])
        self.lea.call("POST", f"/api/podcasts/episodes/{e['id']}/progress", {"position": 101000, "duration": 102000})
        self.assertEqual(self.lea.call("GET", "/api/podcasts")[1]["podcasts"][0]["unplayed"], 1)
        # nouvel épisode : la tâche le récupère et prévient les abonnés
        FakeHost.episodes = 3
        self.sent.clear()
        tm = self.ctx.tasks
        tm._execute(tasks.Job(tm.tasks["podcasts"], "test"))
        self.assertEqual(tm.last("podcasts")["status"], "ok", tm.last("podcasts"))
        self.assertEqual(len(self.sent), 2)  # les deux abonnés
        self.assertEqual(self.sent[0][2], "Épisode 3")
        # un autre compte ne voit pas les épisodes d'un podcast qu'il ne suit pas
        other = Client(self.base)
        self.admin.call("POST", "/api/admin/users", {"username": "tom", "password": "motdepasse3"})
        other.call("POST", "/api/login", {"username": "tom", "password": "motdepasse3"})
        self.assertEqual(other.call("GET", f"/api/podcasts/{p['id']}")[0], 404)
        self.assertEqual(other.call("GET", e["audio"], raw=True)[0], 404)
        self.assertEqual(self.lea.call("DELETE", f"/api/podcasts/{p['id']}")[0], 200)
        self.assertEqual(self.lea.call("GET", "/api/podcasts")[1]["podcasts"], [])

    def test_11_search_and_redirect_guard(self):
        st, r = self.lea.call("GET", "/api/podcasts/search?q=cinema")
        self.assertEqual(st, 200, r)
        self.assertEqual(r["results"][0]["title"], "Résultat cinema")
        st, data, _ = self.lea.call("GET", r["results"][0]["image"], raw=True)  # image d'un résultat de recherche
        self.assertEqual(st, 200)
        with self.assertRaises(podcasts.PodcastError):
            podcasts.check_url("http://127.0.0.1/x")
        with self.assertRaises(podcasts.PodcastError):
            podcasts.check_url("file:///etc/passwd", allow_private=True)
        # une redirection vers le réseau local est refusée même si la première adresse est permise
        with self.assertRaises(podcasts.PodcastError):
            podcasts.fetch(self.hbase + "/redirect", 1000, allow_private=False)

    def test_12_parse_feed(self):
        self.assertEqual(podcasts.duration_ms("1:02:03"), 3723000)
        self.assertEqual(podcasts.duration_ms("95"), 95000)
        with self.assertRaises(podcasts.PodcastError):
            podcasts.parse_feed(b"<html>pas un flux</html>")


if __name__ == "__main__":
    unittest.main()
