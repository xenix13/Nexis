"""Limites par compte (lectures simultanées, débit hors de la maison, temps d'écran) et alertes des administrateurs.

python3 -m unittest tests.test_limits_alerts
"""
import os
import shutil
import tempfile
import threading
import time
import types
import unittest

from tests.test_api import STATIC, Client, media_root, wait_scan

from seance import limits
from seance.api import Ctx, build_app
from seance.web import Server, make_handler

CAPS = {"video": ["h264", "hevc"], "audio": ["aac", "ac3", "eac3"], "containers": ["mp4", "matroska"]}


class UnitLimitsTest(unittest.TestCase):
    def test_bedtime_window(self):
        at = lambda hh, mm: time.mktime((2026, 11, 6, hh, mm, 0, 0, 0, -1))
        self.assertTrue(limits.in_bedtime("21:00-07:00", at(22, 30)))
        self.assertTrue(limits.in_bedtime("21:00-07:00", at(6, 59)))
        self.assertFalse(limits.in_bedtime("21:00-07:00", at(7, 0)))
        self.assertFalse(limits.in_bedtime("21:00-07:00", at(15, 0)))
        self.assertTrue(limits.in_bedtime("13:00-15:00", at(14, 0)))  # sieste : plage dans la journée
        self.assertFalse(limits.in_bedtime(None))

    def test_valid_limits(self):
        self.assertEqual(limits.valid_limits({"max_streams": 2, "remote_kbps": 4000, "daily_minutes": 90, "bedtime": "20:30-07:15"}),
                         {"max_streams": 2, "remote_kbps": 4000, "daily_minutes": 90, "bedtime": "20:30-07:15"})
        self.assertEqual(limits.valid_limits({"max_streams": None, "bedtime": ""}), {"max_streams": None, "bedtime": None})
        for bad in ({"max_streams": 0.5}, {"max_streams": 99}, {"daily_minutes": 5}, {"bedtime": "25:00-07:00"},
                    {"bedtime": "21:00-21:00"}, {"remote_kbps": True}):
            with self.assertRaises(ValueError):
                limits.valid_limits(bad)

    def test_cap_quality(self):
        probe = {"video": {"height": 2160}, "bitrate": 40_000_000, "duration": 100}
        self.assertEqual(limits.cap_quality("original", probe, 6000), "720p")
        self.assertEqual(limits.cap_quality("480p", probe, 20000), "480p")  # déjà en dessous : gardée
        self.assertEqual(limits.cap_quality("1080p", probe, 2500), "480p")

    def test_remote_cap_only_outside(self):
        lim = limits.Limits(types.SimpleNamespace())
        user = {"remote_kbps": 4000}
        self.assertIsNone(lim.remote_cap(user, types.SimpleNamespace(client_ip="192.168.1.30")))
        self.assertEqual(lim.remote_cap(user, types.SimpleNamespace(client_ip="8.8.8.8")), 4000)
        self.assertIsNone(lim.remote_cap({"remote_kbps": None}, types.SimpleNamespace(client_ip="8.8.8.8")))


class LimitsAlertsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        cls.films = os.path.join(cls.work, "films")
        shutil.copytree(os.path.join(media_root(), "films"), cls.films)
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.srv = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        st, r = cls.admin.call("POST", "/api/admin/libraries", {"name": "Films", "kind": "movie", "paths": [cls.films]})
        assert st == 200, r
        cls.lib = r["id"] if isinstance(r, dict) and "id" in r else None
        wait_scan(cls.admin)
        lib = cls.admin.call("GET", "/api/libraries")[1][0]["id"]
        cls.movies = {m["title"]: m for m in cls.admin.call("GET", f"/api/libraries/{lib}/items")[1]["items"]}
        st, u = cls.admin.call("POST", "/api/admin/users", {"username": "zoe", "display_name": "Zoé", "password": "motdepasse4"})
        assert st == 200, u
        cls.zoe_id = u["id"]
        cls.zoe = Client(cls.base)
        assert cls.zoe.call("POST", "/api/login", {"username": "zoe", "password": "motdepasse4"})[0] == 200
        cls.sent = []
        cls.ctx.push.notify = lambda uid, title, body, url="#/", tag="": cls.sent.append((uid, title, body, url)) or 1

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def set_zoe(self, **fields):
        st, r = self.admin.call("PUT", f"/api/admin/users/{self.zoe_id}", fields)
        self.assertEqual(st, 200, r)
        return r

    def play(self, client=None, **extra):
        m = next(v for k, v in self.movies.items() if "Voyage" in k)
        return (client or self.zoe).call("POST", "/api/play", {"id": m["id"], "caps": CAPS, **extra})

    def test_01_admin_fields(self):
        r = self.set_zoe(max_streams=1, remote_kbps=4000, daily_minutes=60, bedtime="21:00-07:00")
        u = next(x for x in self.admin.call("GET", "/api/admin/users")[1] if x["id"] == self.zoe_id)
        self.assertEqual((u["max_streams"], u["remote_kbps"], u["daily_minutes"], u["bedtime"], u["screen_used"]),
                         (1, 4000, 60, "21:00-07:00", 0))
        st, r = self.admin.call("PUT", f"/api/admin/users/{self.zoe_id}", {"bedtime": "minuit"})
        self.assertEqual(st, 400)
        self.assertIn("coucher", r["error"])
        self.set_zoe(max_streams=None, remote_kbps=None, daily_minutes=None, bedtime=None)

    def test_02_simultaneous_streams(self):
        self.set_zoe(max_streams=1)
        try:
            st, a = self.play()
            self.assertEqual(st, 200, a)
            st, r = self.play()
            self.assertEqual(st, 429)
            self.assertIn("1 vidéo(s) en même temps", r["error"])
            st, b = self.play(replace=a["stream"])  # changement de qualité ou de piste : remplace le flux
            self.assertEqual(st, 200, b)
            self.assertEqual(self.play(self.admin)[0], 200)  # un autre compte n'est pas concerné
        finally:
            self.set_zoe(max_streams=None)
            self.ctx.db.run("DELETE FROM streams")

    def test_03_screen_time(self):
        self.set_zoe(daily_minutes=30)
        try:
            st, a = self.play()
            self.assertEqual(st, 200, a)
            m = next(v for k, v in self.movies.items() if "Voyage" in k)
            for state in ("playing", "playing"):
                st, r = self.zoe.call("POST", "/api/progress", {"id": m["id"], "position": 60000, "duration": 600000,
                                                                 "stream": a["stream"], "state": state})
                self.assertEqual(st, 200, r)
            self.assertEqual((r["screen"]["blocked"], r["screen"]["left"]), (False, 30))
            # 29 minutes déjà regardées aujourd'hui (sur d'autres appareils)
            self.ctx.db.run("INSERT OR REPLACE INTO screen_time(user_id, day, seconds) VALUES(?,?,?)",
                            (self.zoe_id, limits.today(), 29 * 60))
            st, r = self.zoe.call("POST", "/api/progress", {"id": m["id"], "position": 70000, "duration": 600000,
                                                             "stream": a["stream"], "state": "playing"})
            self.assertEqual(r["screen"]["left"], 1)
            self.ctx.db.run("INSERT OR REPLACE INTO screen_time(user_id, day, seconds) VALUES(?,?,?)",
                            (self.zoe_id, limits.today(), 31 * 60))
            st, r = self.zoe.call("POST", "/api/progress", {"id": m["id"], "position": 80000, "duration": 600000,
                                                             "stream": a["stream"], "state": "playing"})
            self.assertEqual((r["screen"]["blocked"], r["screen"]["reason"]), (True, "daily"))
            st, r = self.play()
            self.assertEqual(st, 403)
            self.assertIn("Temps d'écran du jour écoulé (30 min)", r["error"])
            self.assertEqual(self.play(self.admin)[0], 200)
            # en anglais
            st, r = self.zoe.call("POST", "/api/play", {"id": m["id"], "caps": CAPS}, headers={"X-Lang": "en"})
            self.assertIn("screen time is up", r["error"])
        finally:
            self.set_zoe(daily_minutes=None)
            self.ctx.db.run("DELETE FROM streams")
            self.ctx.db.run("DELETE FROM screen_time")

    def test_04_bedtime(self):
        now = time.localtime()
        start = f"{now.tm_hour:02d}:00"
        end = f"{(now.tm_hour + 2) % 24:02d}:00"
        self.set_zoe(bedtime=f"{start}-{end}")
        try:
            st, r = self.play()
            self.assertEqual(st, 403)
            self.assertIn(f"reprendra à {end}", r["error"])
        finally:
            self.set_zoe(bedtime=None)

    def test_10_folder_alert(self):
        h = self.ctx.health
        self.sent.clear()
        h.check()
        self.assertEqual(h.active(), [])
        moved = self.films + ".off"
        os.rename(self.films, moved)
        os.makedirs(self.films)  # point de montage vide : partage démonté
        try:
            h.check()
            active = h.active()
            self.assertEqual([a["kind"] for a in active], ["folder"])
            self.assertEqual(len(self.sent), 1)
            self.assertEqual(self.sent[0][1], "Dossier de bibliothèque inaccessible")
            h.check()
            self.assertEqual(len(self.sent), 1)  # pas de nouvel envoi tant que ça dure (rappel au bout d'un jour)
            st, v = self.admin.call("GET", "/api/admin/alerts")
            self.assertEqual(len(v["alerts"]), 1)
            st, v = self.admin.call("GET", "/api/admin/alerts", headers={"X-Lang": "en"})
            self.assertEqual(v["alerts"][0]["title"], "Library folder unreachable")
            self.assertEqual(Client(self.base).call("GET", "/api/admin/alerts")[0], 401)
            self.assertEqual(self.zoe.call("GET", "/api/admin/alerts")[0], 403)
        finally:
            os.rmdir(self.films)
            os.rename(moved, self.films)
        h.check()
        self.assertEqual(h.active(), [])  # résolue d'elle-même

    def test_11_task_and_disk_alerts(self):
        h = self.ctx.health
        self.sent.clear()
        task = self.ctx.tasks.tasks["backup"]
        h.task_finished(task, "error", "disque plein")
        self.assertEqual([a["key"] for a in h.active()], ["task:backup"])
        self.assertIn("disque plein", self.sent[-1][2])
        h.task_finished(task, "ok", "")
        self.assertEqual(h.active(), [])
        # seuil énorme : le disque des données passe pour presque plein
        st, v = self.admin.call("PUT", "/api/admin/alerts", {"disk_gb": 5000})
        self.assertEqual(st, 200, v)
        st, v = self.admin.call("POST", "/api/admin/alerts/check")
        self.assertEqual([a["kind"] for a in v["alerts"]], ["disk"])
        st, v = self.admin.call("PUT", "/api/admin/alerts", {"disk": False})  # coupée : l'alerte disparaît
        self.assertEqual(v["alerts"], [])
        self.admin.call("PUT", "/api/admin/alerts", {"disk": True, "disk_gb": 10})
        self.assertEqual(self.admin.call("PUT", "/api/admin/alerts", {"disk_gb": 0})[0], 400)
        # une tâche qui échoue vraiment
        self.ctx.db.set_setting("backup_dir", "/proc/nexis-impossible")
        try:
            from seance import tasks
            self.ctx.tasks._execute(tasks.Job(task, "test"))
        finally:
            self.ctx.db.set_setting("backup_dir", None)
        self.assertEqual([a["key"] for a in h.active()], ["task:backup"])
        h.resolve("task:backup")


if __name__ == "__main__":
    unittest.main()
