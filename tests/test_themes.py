"""Magasin de thèmes : format vérifié (ni code ni image), thèmes fournis, index en ligne, thèmes installés.

python3 -m unittest tests.test_themes
"""
import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.test_api import STATIC, Client

from seance import themes
from seance.api import Ctx, build_app
from seance.web import Server, make_handler

NEON = {"schema": 1, "id": "essai-neon", "name": {"fr": "Essai néon", "en": "Neon test"}, "author": "quelquun", "version": 1,
        "dark": True, "colors": {"bg": "#07060d", "surface": "#120f22", "surface2": "#1d1836", "text": "#f3f0ff",
                                 "muted": "#a9a1cf", "accent": "#ff2fd1", "accent2": "#1fe6ff", "on_accent": "#1a0016"},
        "effects": ["neon"], "font": "mono", "radius": 4}


class Index(BaseHTTPRequestHandler):
    body = b""
    hits = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        Index.hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(Index.body)))
        self.end_headers()
        self.wfile.write(Index.body)


class ThemesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        cls.host = ThreadingHTTPServer(("127.0.0.1", 0), Index)
        threading.Thread(target=cls.host.serve_forever, daemon=True).start()
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.srv = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.admin = Client(base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        cls.admin.call("POST", "/api/admin/users", {"username": "lea", "password": "motdepasse2"})
        cls.lea = Client(base)
        cls.lea.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})
        # index en ligne : celui d'un faux dépôt (adresse posée directement : l'interface n'accepte que https)
        cls.ctx.db.set_setting("themes_index_url", f"http://127.0.0.1:{cls.host.server_address[1]}/index.json")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.host.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def problems(self, t):
        with self.assertRaises(themes.ThemeError) as cm:
            themes.validate(t)
        return " ".join(cm.exception.problems)

    def test_01_format(self):
        t = themes.validate(NEON)
        self.assertEqual((t["id"], t["font"], t["effects"]), ("essai-neon", "mono", ["neon"]))
        # pas de CSS, pas d'image, pas de champ inconnu
        for change, expect in (
                ({"css": "body{}"}, "champs inconnus"),
                ({"colors": dict(NEON["colors"], bg="url(http://x/y.png)")}, "colors.bg"),
                ({"colors": dict(NEON["colors"], text="red")}, "colors.text"),
                ({"colors": dict(NEON["colors"], extra="#ffffff")}, "couleurs inconnues"),
                ({"background": {"type": "image", "url": "https://exemple.org/a.jpg"}}, "background"),
                ({"effects": ["neon", "javascript"]}, "effects"),
                ({"font": "Comic Sans"}, "font"),
                ({"id": "Pas Bon"}, "id"),
                ({"name": "<script>alert(1)</script>"}, "name"),
                ({"radius": 99}, "radius"),
                ({"style": "barre"}, "style"),
                ({"author": "pas un identifiant"}, "author")):
            self.assertIn(expect, self.problems(dict(NEON, **change)), change)
        # lisibilité : texte trop pâle, bouton illisible, « dark » faux
        self.assertIn("contraste texte / fond", self.problems(dict(NEON, colors=dict(NEON["colors"], text="#333344"))))
        self.assertIn("boutons", self.problems(dict(NEON, colors=dict(NEON["colors"], on_accent="#ff00cc"))))
        self.assertIn("dark", self.problems(dict(NEON, dark=False)))

    def test_02_bundled_themes(self):
        """Thèmes fournis : tous valides, et index.json à jour (sinon : python3 -m seance.themes build static/themes)."""
        path = os.path.join(STATIC, "themes")
        ts, errors = themes.load_dir(path)
        self.assertEqual(errors, [])
        self.assertGreaterEqual(len(ts), 10)
        self.assertEqual(themes.main(["themes", "check", path]), 0)
        self.assertIn("ville-neon", [t["id"] for t in ts])

    def test_03_store(self):
        bad = dict(NEON, id="mauvais", colors=dict(NEON["colors"], text="#101010"))
        with open(os.path.join(STATIC, "themes", "terminal.json"), encoding="utf-8") as f:
            newer = dict(json.load(f), version=7)
        Index.body = json.dumps({"schema": 1, "themes": [NEON, bad, newer, "pas un thème"]}).encode()
        st, r = self.admin.call("GET", "/api/themes?refresh=1")
        self.assertEqual(st, 200, r)
        ids = [t["id"] for t in r["themes"]]
        self.assertIn("essai-neon", ids)                 # thème de la communauté
        self.assertNotIn("mauvais", ids)                  # illisible : écarté, les autres restent
        term = next(t for t in r["themes"] if t["id"] == "terminal")
        self.assertEqual((term["version"], term["source"]), (7, "bundled"))  # version plus récente du magasin
        self.assertEqual((r["store"]["count"], r["store"]["error"]), (1, None))
        # un compte (pas administrateur) ne force pas la relecture
        hits = Index.hits
        self.lea.call("GET", "/api/themes?refresh=1")
        self.assertEqual(Index.hits, hits)
        # magasin injoignable : thèmes fournis seulement, et le dernier index connu reste
        Index.body = b"{pas du json"
        st, r = self.admin.call("GET", "/api/themes?refresh=1")
        self.assertTrue(r["store"]["error"])
        self.assertIn("essai-neon", [t["id"] for t in r["themes"]])
        # désactivé par l'administrateur
        st, c = self.admin.call("PUT", "/api/admin/themes", {"enabled": False})
        self.assertEqual((st, c["enabled"]), (200, False))
        self.assertNotIn("essai-neon", [t["id"] for t in self.lea.call("GET", "/api/themes")[1]["themes"]])
        self.admin.call("PUT", "/api/admin/themes", {"enabled": True})
        for url in ("http://exemple.org/index.json", "file:///etc/passwd", "https://exemple.org/a b"):
            self.assertEqual(self.admin.call("PUT", "/api/admin/themes", {"index_url": url})[0], 400, url)
        self.assertEqual(self.lea.call("PUT", "/api/admin/themes", {"enabled": False})[0], 403)

    def test_04_install(self):
        c = self.lea
        st, r = c.call("POST", "/api/themes/check", {"theme": dict(NEON, css="x")})
        self.assertEqual(st, 400)
        self.assertIn("champs inconnus", " ".join(r["problems"]))
        st, r = c.call("PUT", "/api/me/prefs", {"my_themes": [NEON], "theme": "t:essai-neon", "accent": "#ff2fd1"})
        self.assertEqual((st, r["theme"], r["my_themes"][0]["id"]), (200, "t:essai-neon", "essai-neon"))
        twice = [NEON, copy.deepcopy(NEON)]
        for bad in ({"my_themes": twice}, {"my_themes": [dict(NEON, css="x")]}, {"my_themes": [NEON] * 21},
                    {"theme": "t:Pas bon"}, {"theme": "ville-neon"}):
            self.assertEqual(c.call("PUT", "/api/me/prefs", bad)[0], 400, list(bad))
        self.assertEqual(c.call("GET", "/api/me")[1]["prefs"]["theme"], "t:essai-neon")
        c.call("POST", "/api/me/prefs/reset")
        p = c.call("GET", "/api/me")[1]["prefs"]
        self.assertEqual((p["theme"], p["my_themes"]), ("dark", []))


if __name__ == "__main__":
    unittest.main()
