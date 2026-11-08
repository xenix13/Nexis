"""Livres audio (bibliothèque).

python3 -m unittest tests.test_books
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

from tests.test_api import STATIC, Client, wait_scan

from seance.api import Ctx, build_app
from seance.web import Server, make_handler

def tone(path, seconds, **tags):
    meta = [a for k, v in tags.items() for a in ("-metadata", f"{k}={v}")]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "libmp3lame", "-b:a", "32k", *meta, path], check=True)


class BooksTest(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
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


if __name__ == "__main__":
    unittest.main()
