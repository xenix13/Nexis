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

FILES = {"noxa-android.apk": b"PK" + b"a" * 5000, "Noxa-Setup.exe": b"MZ" + b"w" * 7000, "latest.yml": b"version: 1.0.0\npath: Noxa-Setup.exe\n",
         "noxa-apps.json": json.dumps({"desktop": "1.0.0", "android": {"version": "1.0.0", "code": 1}}).encode()}


class FakeGitHub(BaseHTTPRequestHandler):
    hits = []
    complete = True  # False : publication de cette version sans les applications (jointes plus tard par la CI)
    missing = False  # True : cette version pas encore publiée du tout

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeGitHub.hits.append(self.path)
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        assets = [{"name": n, "size": len(d), "browser_download_url": f"{base}/dl/{n}"} for n, d in FILES.items()]
        deb = [{"name": f"noxa_{__version__}_all.deb", "size": 10, "browser_download_url": f"{base}/dl/deb"}]
        if self.path == f"/repos/xenix13/Noxa/releases/tags/v{__version__}" and not FakeGitHub.missing:
            return self.send(200, json.dumps({"tag_name": f"v{__version__}", "assets": assets if FakeGitHub.complete else deb}).encode())
        if self.path.startswith("/repos/xenix13/Noxa/releases?"):
            return self.send(200, json.dumps([
                {"tag_name": f"v{__version__}", "assets": assets if FakeGitHub.complete else deb},
                {"tag_name": "apps-test", "prerelease": True, "assets": assets},
                {"tag_name": "v3.99.0", "assets": assets},
                {"tag_name": "v3.57.1", "assets": [{"name": "nexis-android.apk", "size": 1, "browser_download_url": "x"}]}]).encode())
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
        self.assertEqual(body, FILES["noxa-android.apk"])
        for _ in range(50):
            if apps.os.path.isfile(os.path.join(self.ctx.data_dir, "apps", f"v{__version__}", "noxa-android.apk")):
                break
            time.sleep(0.1)
        hits = len(FakeGitHub.hits)
        st, body, h = self.anon.call("GET", "/apps/android", raw=True)
        self.assertEqual((st, body), (200, FILES["noxa-android.apk"]))
        self.assertEqual(len(FakeGitHub.hits), hits)  # servi par le serveur lui-même
        self.assertEqual(h["Content-Type"], "application/vnd.android.package-archive")
        self.assertIn("attachment", h.get("Content-Disposition", ""))
        self.assertEqual(self.anon.call("GET", "/apps/macos")[0], 404)  # pas encore publiée
        self.assertEqual(self.anon.call("GET", "/apps/inconnue")[0], 404)

    def test_03_desktop_updates(self):
        self.anon.call("GET", "/api/apps")
        st, body, h = self.anon.call("GET", "/apps/desktop/latest.yml", raw=True)
        self.assertEqual((st, body), (200, FILES["latest.yml"]))
        for bad in ("noxa-apps.json", "..%2Fseance.db", "autre.yml"):
            self.assertEqual(self.anon.call("GET", f"/apps/desktop/{bad}")[0], 404, bad)


    def test_04_release_without_apps_yet(self):
        """Serveur mis à jour avant que la CI ait joint les applications à la publication : il propose celles de
        la version précédente, et regarde de nouveau dans 10 minutes (pas 6 heures)."""
        for missing in (False, True):
            FakeGitHub.complete, FakeGitHub.missing = False, missing
            try:
                a = apps.Apps(self.ctx)
                a.refresh()
                self.assertEqual(a.release["tag"], "v3.99.0", missing)  # ni la pré-version de test, ni l'ancien nom
                self.assertEqual(a.manifest["android"]["code"], 1)
                self.assertLessEqual(time.time() - a.checked, apps.REFRESH - apps.RETRY + 5)
                a.refresh()  # avant les 10 minutes : rien de nouveau demandé
                self.assertEqual(a.release["tag"], "v3.99.0")
                FakeGitHub.complete, FakeGitHub.missing = True, False
                a.checked -= apps.RETRY + 1
                a.refresh()
                self.assertEqual(a.release["tag"], f"v{__version__}")
                self.assertGreater(a.checked, time.time() - 5)  # complète : prochain passage dans 6 h
            finally:
                FakeGitHub.complete, FakeGitHub.missing = True, False


if __name__ == "__main__":
    unittest.main()
