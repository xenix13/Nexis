"""Sous-titres générés par Whisper : faux whisper-cli (whisper.cpp) et faux service compatible OpenAI.

python3 -m unittest tests.test_whisper
"""
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.test_api import STATIC, Client, media_root, wait_scan

from seance import tasks, whisper
from seance.api import Ctx, build_app
from seance.web import Server, make_handler

FAKE_CLI = """#!/usr/bin/env python3
import sys
args = sys.argv[1:]
out = args[args.index("-of") + 1]
lang = args[args.index("-l") + 1]
for p in (10, 50, 100):
    print(f"whisper_print_progress_callback: progress = {p}%", flush=True)
open(out + ".srt", "w").write("1\\n00:00:00,500 --> 00:00:02,000\\nTexte reconnu (" + lang + ")\\n\\n")
"""


class FakeTranscriber(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        FakeTranscriber.calls.append((self.path, self.headers.get("Authorization"), body))
        if self.headers.get("Authorization") != "Bearer sk-test":
            self.send_response(401)
            self.end_headers()
            return
        if b'name="response_format"\r\n\r\nsrt' in body:  # ce faux service ne sait faire que du JSON
            self.send_response(400)
            self.end_headers()
            return
        data = json.dumps({"segments": [{"start": 0.2, "end": 1.5, "text": " Bonjour "}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class WhisperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        cls.bin = os.path.join(cls.work, "bin")
        os.makedirs(cls.bin)
        cli = os.path.join(cls.bin, "whisper-cli")
        with open(cli, "w") as f:
            f.write(FAKE_CLI)
        os.chmod(cli, os.stat(cli).st_mode | stat.S_IEXEC)
        cls.saved_path = os.environ["PATH"]
        os.environ["PATH"] = cls.bin + os.pathsep + cls.saved_path
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeTranscriber)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
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
        cls.movies = {m["title"]: m for m in cls.admin.call("GET", f"/api/libraries/{lib}/items")[1]["items"]}

    @classmethod
    def tearDownClass(cls):
        os.environ["PATH"] = cls.saved_path
        cls.srv.shutdown()
        cls.fake.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def row(self, title):
        m = next(v for k, v in self.movies.items() if title in k)
        return self.ctx.db.one("SELECT * FROM items WHERE id=?", (int(m["id"]),))

    def test_01_needs(self):
        probe = {"audio": [{"index": 1, "lang": "fra", "default": True}], "subs": [{"lang": "eng"}], "external": []}
        self.assertEqual(whisper.Whisper.needs(probe), "fra")
        probe["external"].append({"lang": "fra"})
        self.assertIsNone(whisper.Whisper.needs(probe))
        self.assertEqual(whisper.Whisper.needs({"audio": [{"index": 1, "lang": ""}]}), "und")
        self.assertIsNone(whisper.Whisper.needs({"audio": [{"index": 1, "lang": ""}], "subs": [{"lang": "eng"}]}))
        self.assertIsNone(whisper.Whisper.needs({"audio": []}))
        srt = whisper.to_srt([{"s": 3723004, "e": 3725000, "t": "Salut"}]).decode()
        self.assertEqual(srt, "1\n01:02:03,004 --> 01:02:05,000\nSalut\n\n")

    def test_02_local_cli(self):
        st, v = self.admin.call("GET", "/api/admin/whisper")
        self.assertEqual((v["cli"], v["available"], v["backend"]), (True, False, None))  # pas encore de modèle
        os.makedirs(os.path.join(self.ctx.data_dir, "whisper"), exist_ok=True)
        with open(self.ctx.whisper.model_path("base"), "wb") as f:
            f.write(b"\0" * 1024)
        st, v = self.admin.call("GET", "/api/admin/whisper")
        self.assertEqual((v["available"], v["backend"], v["models"]["base"]["installed"]), (True, "cli", True))
        self.assertTrue(self.admin.call("GET", "/api/me")[1]["features"]["whisper"])
        r = self.row("Brume")
        seen = []
        entry = self.ctx.whisper.generate(r, progress=seen.append)
        self.assertEqual(entry["dl"], "whisper")
        self.assertGreater(seen[-1], 0.9)
        st, d = self.admin.call("GET", f"/api/items/{r['id']}")
        sub = next(s for s in d["subtitles"] if s.get("generated"))
        self.assertIn("généré", sub["title"])
        st, cues = self.admin.call("GET", f"/api/items/{r['id']}/subtitles/{sub['id']}")
        self.assertTrue(cues["cues"][0]["t"].startswith("Texte reconnu"))
        # retiré à la main : la tâche ne le refait pas
        self.assertEqual(self.admin.call("DELETE", f"/api/items/{r['id']}/whisper")[0], 200)
        tm = self.ctx.tasks
        tm._execute(tasks.Job(tm.tasks["whisper"], "test"))
        st, d = self.admin.call("GET", f"/api/items/{r['id']}")
        self.assertFalse(any(s.get("generated") for s in d["subtitles"]))
        last = tm.last("whisper")
        self.assertEqual(last["status"], "ok", last)

    def test_03_manual_queue(self):
        r = self.row("Voyage")
        st, q = self.admin.call("POST", f"/api/items/{r['id']}/whisper")
        self.assertEqual(st, 200, q)
        deadline = time.time() + 60
        while time.time() < deadline:
            st, d = self.admin.call("GET", f"/api/items/{r['id']}")
            if any(s.get("generated") for s in d["subtitles"]):
                break
            time.sleep(0.3)
        else:
            self.fail("sous-titres non générés")
        lea = Client(self.base)
        self.admin.call("POST", "/api/admin/users", {"username": "lea", "password": "motdepasse2"})
        lea.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})
        self.assertEqual(lea.call("POST", f"/api/items/{r['id']}/whisper")[0], 403)  # réservé aux administrateurs
        self.assertFalse(lea.call("GET", "/api/me")[1]["features"]["whisper"])

    def test_04_http_service(self):
        port = self.fake.server_address[1]
        st, v = self.admin.call("PUT", "/api/admin/whisper", {"url": "ftp://x"})
        self.assertEqual(st, 400)
        st, v = self.admin.call("PUT", "/api/admin/whisper", {"url": f"http://127.0.0.1:{port}", "key": "sk-test",
                                                              "model_http": "Systran/faster-whisper-small"})
        self.assertEqual((st, v["backend"], v["key_set"]), (200, "http", True))
        self.assertNotIn("sk-test", json.dumps(v))
        saved = whisper.CHUNK
        whisper.CHUNK = 2  # plusieurs morceaux : repères décalés de la position de chaque morceau
        try:
            r = self.row("Polaire")
            self.ctx.whisper.generate(r)
        finally:
            whisper.CHUNK = saved
        posts = [c for c in FakeTranscriber.calls if c[0] == "/v1/audio/transcriptions"]
        self.assertGreaterEqual(len(posts), 2)
        self.assertIn(b"Systran/faster-whisper-small", posts[0][2])
        st, d = self.admin.call("GET", f"/api/items/{r['id']}")
        sub = next(s for s in d["subtitles"] if s.get("generated"))
        st, cues = self.admin.call("GET", f"/api/items/{r['id']}/subtitles/{sub['id']}")
        starts = [c["s"] for c in cues["cues"]]
        self.assertEqual(starts[:2], [200, 2200])
        # mauvaise clé
        self.admin.call("PUT", "/api/admin/whisper", {"key": "sk-faux"})
        with self.assertRaises(whisper.WhisperError):
            self.ctx.whisper.generate(r)
        self.admin.call("PUT", "/api/admin/whisper", {"url": "", "clear_key": True})


if __name__ == "__main__":
    unittest.main()
