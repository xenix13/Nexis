"""Applications natives distribuées par le serveur : liste, fichiers (copie locale ou GitHub en attendant),
mises à jour de l'application de bureau.

python3 -m unittest tests.test_apps
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.test_api import STATIC, Client

from seance import __version__, apps
from seance.api import Ctx, build_app
from seance.web import Server, make_handler

FILES = {"nexis-android.apk": b"PK" + b"a" * 5000, "Nexis-Setup.exe": b"MZ" + b"w" * 7000, "latest.yml": b"version: 1.0.0\npath: Nexis-Setup.exe\n",
         "nexis-apps.json": json.dumps({"desktop": "1.0.0", "android": {"version": "1.0.0", "code": 1}}).encode()}


class FakeGitHub(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeGitHub.hits.append(self.path)
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if self.path == f"/repos/xenix13/Nexis/releases/tags/v{__version__}":
            body = json.dumps({"tag_name": f"v{__version__}", "assets": [
                {"name": n, "size": len(d), "browser_download_url": f"{base}/dl/{n}"} for n, d in FILES.items()]}).encode()
            return self.send(200, body)
        if self.path.startswith("/dl/") and self.path[4:] in FILES:
            return self.send(200, FILES[self.path[4:]])
        self.send(404, b"{}")

    def send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class AppsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        cls.gh = ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=cls.gh.serve_forever, daemon=True).start()
        cls.saved = apps.API
        apps.API = f"http://127.0.0.1:{cls.gh.server_address[1]}"
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.srv = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.anon = Client(cls.base)  # sans compte : la page Applications est publique

    @classmethod
    def tearDownClass(cls):
        apps.API = cls.saved
        cls.srv.shutdown()
        cls.gh.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_01_listing(self):
        st, r = self.anon.call("GET", "/api/apps")
        self.assertEqual(st, 200, r)
        by = {a["id"]: a for a in r["apps"]}
        self.assertTrue(by["android"]["available"])
        self.assertTrue(by["windows"]["available"])
        self.assertFalse(by["macos"]["available"])  # pas dans cette publication
        self.assertEqual(r["versions"]["android"]["code"], 1)
        self.assertEqual(r["release"], f"v{__version__}")

    def test_02_files(self):
        self.anon.call("GET", "/api/apps")
        # premier téléchargement : GitHub le temps que le serveur fasse sa copie
        st, body, h = self.anon.call("GET", "/apps/android", raw=True)
        self.assertIn(st, (200,), h)  # urllib suit la redirection
        self.assertEqual(body, FILES["nexis-android.apk"])
        for _ in range(50):
            if apps.os.path.isfile(os.path.join(self.ctx.data_dir, "apps", f"v{__version__}", "nexis-android.apk")):
                break
            time.sleep(0.1)
        hits = len(FakeGitHub.hits)
        st, body, h = self.anon.call("GET", "/apps/android", raw=True)
        self.assertEqual((st, body), (200, FILES["nexis-android.apk"]))
        self.assertEqual(len(FakeGitHub.hits), hits)  # servi par le serveur lui-même
        self.assertEqual(h["Content-Type"], "application/vnd.android.package-archive")
        self.assertIn("attachment", h.get("Content-Disposition", ""))
        self.assertEqual(self.anon.call("GET", "/apps/macos")[0], 404)  # pas encore publiée
        self.assertEqual(self.anon.call("GET", "/apps/inconnue")[0], 404)

    def test_03_desktop_updates(self):
        self.anon.call("GET", "/api/apps")
        st, body, h = self.anon.call("GET", "/apps/desktop/latest.yml", raw=True)
        self.assertEqual((st, body), (200, FILES["latest.yml"]))
        for bad in ("nexis-apps.json", "..%2Fseance.db", "autre.yml"):
            self.assertEqual(self.anon.call("GET", f"/apps/desktop/{bad}")[0], 404, bad)


if __name__ == "__main__":
    unittest.main()
