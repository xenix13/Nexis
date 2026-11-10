"""Sauvegarde / restauration du serveur, et conversion HDR -> SDR au transcodage.

python3 -m unittest tests.test_backup_hdr
"""
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import unittest

from tests.test_api import STATIC, Client

from seance import backup, media, transcoder
from seance.api import Ctx, build_app
from seance.web import Server, make_handler


def start(data):
    ctx = Ctx(data, STATIC)
    srv = Server(("127.0.0.1", 0), make_handler(build_app(ctx)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return ctx, srv, f"http://127.0.0.1:{srv.server_address[1]}"


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.data = os.path.join(self.work, "data")
        self.ctx, self.srv, self.base = start(self.data)
        self.admin = Client(self.base)
        st, r = self.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1"},
                                headers={"X-Setup-Code": self.ctx.setup_code})
        assert st == 200, r

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.work, ignore_errors=True)

    def test_backup_download_upload_restore(self):
        st, r = self.admin.call("POST", "/api/admin/users", {"username": "lea", "display_name": "Léa",
                                                              "password": "motdepasse2"})
        self.assertEqual(st, 200, r)
        os.makedirs(os.path.join(self.data, "avatars"), exist_ok=True)
        with open(os.path.join(self.data, "avatars", "1.jpg"), "wb") as f:
            f.write(b"\xff\xd8avatar")
        st, r = self.admin.call("POST", "/api/admin/backups")
        self.assertEqual(st, 200, r)
        name = r["backup"]["name"]
        self.assertEqual([b["name"] for b in r["backups"]], [name])
        self.assertEqual(r["backups"][0]["version"], __import__("seance").__version__)
        path = os.path.join(self.data, "backups", name)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with tarfile.open(path) as tar:
            names = tar.getnames()
        self.assertEqual(names[0], "manifest.json")
        self.assertIn("seance.db", names)
        self.assertIn("avatars/1.jpg", names)
        # téléchargement, puis envoi (comme depuis un autre serveur)
        st, data, h = self.admin.call("GET", f"/api/admin/backups/{name}", raw=True)
        self.assertEqual((st, h["Content-Type"], data[:2]), (200, "application/gzip", b"\x1f\x8b"))
        self.assertEqual(Client(self.base).call("GET", f"/api/admin/backups/{name}", raw=True)[0], 401)
        st, r = self.admin.call("POST", "/api/admin/backups/upload", raw_body=data, headers={"Content-Type": "application/gzip"})
        self.assertEqual(st, 200, r)
        uploaded = r["backup"]["name"]
        self.assertIn("-envoi", uploaded)
        # une archive qui n'est pas de Noxa, ou qui sort de ses dossiers : refusée
        bad = io.BytesIO()
        with tarfile.open(fileobj=bad, mode="w:gz") as tar:
            for n, content in (("manifest.json", json.dumps({"format": 1, "schema": 1}).encode()), ("seance.db", b"x"),
                               ("../evil", b"x")):
                info = tarfile.TarInfo(n)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        st, r = self.admin.call("POST", "/api/admin/backups/upload", raw_body=bad.getvalue())
        self.assertEqual(st, 400)
        self.assertEqual(len(self.admin.call("GET", "/api/admin/backups")[1]["backups"]), 2)
        self.assertEqual(self.admin.call("GET", "/api/admin/backups/nexis-..%2Fseance.db")[0], 404)
        # après la sauvegarde : un compte en plus, qui disparaîtra à la restauration
        self.admin.call("POST", "/api/admin/users", {"username": "tom", "display_name": "Tom", "password": "motdepasse3"})
        st, r = self.admin.call("POST", f"/api/admin/backups/{uploaded}/restore")
        self.assertEqual((st, r["restarting"]), (200, False))  # pas de superviseur dans les tests
        self.assertTrue(os.path.isfile(os.path.join(self.data, backup.PENDING, "seance.db")))
        self.srv.shutdown()
        self.srv.server_close()
        # redémarrage : la sauvegarde est mise en place avant l'ouverture de la base
        self.ctx, self.srv, self.base = start(self.data)
        self.assertIsNotNone(self.ctx.restored)
        names = {u["username"] for u in self.ctx.db.all("SELECT username FROM users")}
        self.assertEqual(names, {"admin", "lea"})
        self.assertFalse(os.path.exists(os.path.join(self.data, backup.PENDING)))
        self.assertTrue(any(n.startswith("before-restore-") for n in os.listdir(self.data)))
        admin = Client(self.base)
        st, r = admin.call("POST", "/api/login", {"username": "admin", "password": "motdepasse1"})
        self.assertEqual(st, 200, r)
        st, v = admin.call("GET", "/api/admin/backups")
        self.assertTrue(v["restored"])

    def test_settings_schedule_and_prune(self):
        st, r = self.admin.call("PUT", "/api/admin/backups/settings", {"dir": "/nexiste/pas"})
        self.assertEqual(st, 400)
        other = os.path.join(self.work, "nas")
        os.makedirs(other)
        st, r = self.admin.call("PUT", "/api/admin/backups/settings",
                                {"dir": other, "schedule": {"mode": "weekly", "time": "03:15", "weekday": 6}, "keep": 2})
        self.assertEqual((st, r["dir"], r["schedule"]["mode"], r["keep"]), (200, other, "weekly", 2))
        b = backup.create(self.ctx, "auto")
        for i in range(3):
            shutil.copy(os.path.join(other, b["name"]), os.path.join(other, f"nexis-2026010{i + 1}-000000-auto.tar.gz"))
        os.remove(os.path.join(other, b["name"]))
        manual = backup.create(self.ctx)
        backup.prune(self.ctx, 2)
        left = [b["name"] for b in backup.listing(self.ctx)]
        self.assertIn(manual["name"], left)  # une sauvegarde faite à la main n'est jamais supprimée
        self.assertEqual(sum(1 for n in left if "-auto" in n), 2)
        self.ctx.tasks.run_now("backup", "test")
        tasks = {t["id"]: t for t in self.admin.call("GET", "/api/admin/tasks")[1]["tasks"]}
        self.assertIn("backup", tasks)

    def test_newer_schema_refused(self):
        b = backup.create(self.ctx)
        path = os.path.join(self.data, "backups", b["name"])
        work = tempfile.mkdtemp()
        try:
            with tarfile.open(path) as tar:
                tar.extractall(work, filter="data")
            with open(os.path.join(work, "manifest.json")) as f:
                man = json.load(f)
            man["schema"] = 999
            with open(os.path.join(work, "manifest.json"), "w") as f:
                json.dump(man, f)
            newer = os.path.join(self.data, "backups", "nexis-20990101-000000.tar.gz")
            with tarfile.open(newer, "w:gz") as tar:
                tar.add(os.path.join(work, "manifest.json"), "manifest.json")
                tar.add(os.path.join(work, "seance.db"), "seance.db")
            st, r = self.admin.call("POST", "/api/admin/backups/nexis-20990101-000000.tar.gz/restore")
            self.assertEqual(st, 400)
            self.assertIn("plus récente", r["error"])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_console_restore(self):
        b = backup.create(self.ctx)
        from seance.__main__ import main
        rc = main(["--data", self.data, "restore", os.path.join(self.data, "backups", b["name"])])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.data, backup.PENDING, "seance.db")))
        self.assertEqual(main(["--data", self.data, "restore", "/nexiste/pas.tar.gz"]), 1)


class HdrTest(unittest.TestCase):
    def test_tonemap_hdr10(self):
        if not (media.has_filter("zscale") and media.has_filter("tonemap")):
            self.skipTest("ffmpeg sans zscale")
        work = tempfile.mkdtemp()
        try:
            src = os.path.join(work, "hdr.mkv")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24", "-t", "1",
                            "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error",
                            "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc", src], check=True)
            probe = media.probe(src)
            caps = {"video": ["h264"], "audio": ["aac"], "containers": ["mp4"]}
            p = transcoder.plan(probe, caps=caps)
            self.assertEqual((p["mode"], p["hdr"]), ("transcode", "smpte2084"))
            self.assertIn("HDR converti en SDR", p["reasons"])
            out = os.path.join(work, "out.mp4")
            subprocess.run([media.FFMPEG, "-v", "error", "-y", "-i", src, *transcoder.media_args(p), out], check=True)
            r = subprocess.run([media.FFPROBE, "-v", "error", "-select_streams", "v", "-show_entries",
                                "stream=color_transfer,pix_fmt", "-of", "json", out], capture_output=True, check=True)
            s = json.loads(r.stdout)["streams"][0]
            self.assertEqual((s["pix_fmt"], s.get("color_transfer")), ("yuv420p", "bt709"))
            # désactivée : pas de conversion ; source SDR : rien à faire
            off = transcoder.plan(probe, caps=caps, tonemap="off")
            self.assertNotIn("zscale", " ".join(transcoder.media_args(off)))
            self.assertNotIn("HDR converti en SDR", off["reasons"])
            sdr = dict(probe, video=dict(probe["video"], transfer="bt709"))
            self.assertNotIn("zscale", " ".join(transcoder.media_args(transcoder.plan(sdr, caps=caps))))
            # sous-titres image incrustés : vidéo convertie avant d'y poser les sous-titres
            burn = dict(p, burn_index=5)
            fc = transcoder.media_args(burn)[1]
            self.assertLess(fc.index("tonemap"), fc.index("overlay"))
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
