"""Tâches planifiées, images de chapitres, détection des intros, TV en direct, « Regarder ensemble ».

python3 -m unittest tests.test_features
"""
import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from tests.test_api import STATIC, TMDB, Client, media_root, wait_scan  # noqa: F401  (TMDB : variables d'environnement)
from tests.fixtures import ff

from seance import tasks as tasks_mod
from seance.api import Ctx, build_app
from seance.web import Server, make_handler


def tone(path, seconds, a, b, c):
    """Audio « musical » propre à chaque passage (des tons qui varient), pour l'empreinte audio."""
    ff("-f", "lavfi", "-i", f"aevalsrc='0.4*sin(2*PI*t*(220+180*sin(t*{a}+{b})))+0.2*sin(2*PI*t*(660+300*sin(t*{c})))'"
       f":d={seconds}:s=44100", path)


def make_intro_season(root):
    """Trois épisodes qui partagent la même chanson d'ouverture (85 s), à des moments différents."""
    tmp = os.path.join(root, "_tmp")
    season = os.path.join(root, "Sans Chapitres", "Season 01")
    os.makedirs(tmp, exist_ok=True)
    os.makedirs(season, exist_ok=True)
    song = os.path.join(tmp, "song.wav")
    ff("-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*t*(330+110*floor(mod(t*2,6))))+0.3*sin(2*PI*t*(495+55*floor(mod(t*3,5))))'"
       ":d=85:s=44100", song)
    starts = {}
    for n, (pre, a, b, c) in enumerate(((30, 0.7, 1, 1.3), (55, 0.9, 2, 0.4), (20, 0.5, 3, 2.2)), 1):
        p1, p2 = os.path.join(tmp, f"pre{n}.wav"), os.path.join(tmp, f"post{n}.wav")
        tone(p1, pre, a, b, c)
        tone(p2, 60, a * 0.6, b + 1, c * 1.4)
        audio = os.path.join(tmp, f"ep{n}.wav")
        ff("-i", p1, "-i", song, "-i", p2, "-filter_complex", "[0][1][2]concat=n=3:v=0:a=1", audio)
        total = pre + 85 + 60
        ff("-f", "lavfi", "-i", "color=c=0x223344:s=160x90:r=10", "-i", audio, "-t", str(total), "-map", "0:v", "-map", "1:a",
           "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", "-pix_fmt", "yuv420p", "-c:a", "aac",
           os.path.join(season, f"Sans.Chapitres.S01E{n:02d}.mkv"))
        starts[n] = pre
    return starts


class FeaturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        cls.media = os.path.join(cls.work, "media")
        shutil.copytree(media_root(), cls.media)
        from seance import scanner
        scanner.RECENT_SECONDS = 0
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.server = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "colas", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        cls.admin.call("PUT", "/api/admin/settings", {"tmdb_key": TMDB.key})
        cls.libs = {}
        for name, kind, sub in (("Films", "movie", "films"), ("Animes", "show", "animes")):
            wait_scan(cls.admin)
            st, lib = cls.admin.call("POST", "/api/admin/libraries",
                                     {"name": name, "kind": kind, "paths": [os.path.join(cls.media, sub)]})
            assert st == 200, lib
            cls.libs[name] = lib["id"]
        wait_scan(cls.admin)
        st, u = cls.admin.call("POST", "/api/admin/users", {"username": "lea", "password": "motdepasse2",
                                                            "display_name": "Léa", "all_libraries": True})
        assert st == 200, u
        cls.lea = Client(cls.base)
        assert cls.lea.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})[0] == 200

    @classmethod
    def tearDownClass(cls):
        cls.ctx.live.stop_all()
        cls.ctx.transcoder.stop_all()
        cls.server.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    # helpers
    def run_task(self, tid, **options):
        tm = self.ctx.tasks
        if options:
            tm.update(tid, options=options)
        job = tasks_mod.Job(tm.tasks[tid], "test")
        tm._execute(job)
        return tm.last(tid)

    def item(self, title):
        return self.ctx.db.one("SELECT * FROM items WHERE title=?", (title,))

    # ------------------------------------------------------------ tâches
    def test_task_schedule(self):
        anchor = time.mktime((2026, 10, 23, 10, 0, 0, 0, 0, -1))  # vendredi 23/10/2026 10:00 (heure locale)
        nxt = tasks_mod.next_run({"mode": "daily", "time": "03:00"}, None, anchor)
        self.assertEqual(time.localtime(nxt)[:5], (2026, 10, 24, 3, 0))
        nxt = tasks_mod.next_run({"mode": "daily", "time": "18:30"}, anchor, anchor)
        self.assertEqual(time.localtime(nxt)[:5], (2026, 10, 23, 18, 30))
        nxt = tasks_mod.next_run({"mode": "weekly", "weekday": 6, "time": "04:00"}, anchor, anchor)
        self.assertEqual(time.localtime(nxt)[:5], (2026, 10, 25, 4, 0))  # dimanche
        self.assertEqual(tasks_mod.next_run({"mode": "interval", "hours": 12}, anchor, 0), anchor + 12 * 3600)
        self.assertIsNone(tasks_mod.next_run({"mode": "off"}, anchor, anchor))

    def test_tasks_api(self):
        self.assertEqual(self.lea.call("GET", "/api/admin/tasks")[0], 403)
        st, r = self.admin.call("GET", "/api/admin/tasks")
        self.assertEqual(st, 200)
        ids = [t["id"] for t in r["tasks"]]
        self.assertEqual(ids, ["scan", "metadata", "chapters", "intros", "subtitles", "livetv", "cleanup"])
        self.assertEqual(self.admin.call("PUT", "/api/admin/tasks/metadata", {"schedule": {"mode": "daily", "time": "25:00"}})[0], 400)
        self.assertEqual(self.admin.call("PUT", "/api/admin/tasks/metadata", {"options": {"days": 9999}})[0], 400)
        st, t = self.admin.call("PUT", "/api/admin/tasks/metadata", {"schedule": {"mode": "weekly", "weekday": 2, "time": "4:30"},
                                                                      "options": {"scope": "stale", "days": 15}})
        self.assertEqual((st, t["schedule"], t["options"]["scope"], t["options"]["days"]),
                         (200, {"mode": "weekly", "weekday": 2, "time": "04:30"}, "stale", 15), t)
        self.assertEqual(time.localtime(t["next"]).tm_wday, 2)
        self.assertEqual(self.admin.call("POST", "/api/admin/tasks/nope/run")[0], 404)
        # actualisation des métadonnées « incomplètes » (ici : rien à faire ou quelques éléments)
        last = self.run_task("metadata", scope="all", libraries=[self.libs["Films"]])
        self.assertEqual(last["status"], "ok", last)
        self.assertIn("actualisé", last["summary"])
        last = self.run_task("scan")
        self.assertEqual(last["status"], "ok", last)
        last = self.run_task("cleanup")
        self.assertEqual(last["status"], "ok", last)

    # ------------------------------------------------------------ chapitres
    def test_chapter_images(self):
        ep = self.item("Épisode 1") or self.ctx.db.one("SELECT * FROM items WHERE key LIKE '%Kaze no Michi - 01%'")
        st, d = self.admin.call("GET", f"/api/items/{ep['id']}")
        self.assertEqual([c["title"] for c in d["chapters"]], ["Prologue", "Opening", "Partie A", "Ending", "Preview"])
        self.assertTrue(all(c["image"] for c in d["chapters"]))
        # à la demande (la vignette d'un chapitre affichée avant le passage de la tâche)
        st, body, h = self.lea.call("GET", d["chapters"][2]["image"], raw=True)
        self.assertEqual((st, body[:3], h["Content-Type"]), (200, b"\xff\xd8\xff", "image/jpeg"))
        self.assertEqual(self.lea.call("GET", f"/api/items/{ep['id']}/chapters/9.jpg")[0], 404)
        # la tâche fabrique les autres et note le fichier comme traité
        last = self.run_task("chapters", pause_playback=False)
        self.assertEqual(last["status"], "ok", last)
        folder = self.ctx.chapter_images.folder(ep)
        self.assertEqual(sorted(os.listdir(folder)), ["0.jpg", "1.jpg", "2.jpg", "3.jpg", "4.jpg"])
        self.assertTrue(self.ctx.db.scalar("SELECT 1 FROM media_analysis WHERE item_id=? AND kind='chapters'", (ep["id"],)))
        self.assertIn("prêtes", self.run_task("chapters")["summary"])  # rien de neuf au second passage

    # ------------------------------------------------------------ intros par empreinte audio
    def test_intro_detection(self):
        root = os.path.join(self.work, "intro")
        starts = make_intro_season(root)
        st, lib = self.admin.call("POST", "/api/admin/libraries", {"name": "Tests intro", "kind": "show", "paths": [root]})
        self.assertEqual(st, 200, lib)
        wait_scan(self.admin)
        try:
            last = self.run_task("intros", libraries=[lib["id"]], credits=False, pause_playback=False, skip_chaptered=True)
            self.assertEqual(last["status"], "ok", last)
            eps = self.ctx.db.all("SELECT id, idx FROM items WHERE library_id=? AND type='episode' ORDER BY idx", (int(lib["id"]),))
            self.assertEqual(len(eps), 3)
            for e in eps:
                st, d = self.admin.call("GET", f"/api/items/{e['id']}")
                m = d["markers"].get("intro")
                self.assertIsNotNone(m, d["markers"])
                self.assertEqual(m["source"], "audio")
                self.assertAlmostEqual(m["start"] / 1000, starts[e["idx"]], delta=4)
                self.assertAlmostEqual(m["end"] / 1000, starts[e["idx"]] + 85, delta=4)
            # un repère posé à la main n'est jamais écrasé
            self.admin.call("PUT", f"/api/admin/markers/{eps[0]['id']}", {"kind": "intro", "start": 1000, "end": 2000})
            self.ctx.db.run("DELETE FROM media_analysis WHERE item_id=?", (eps[0]["id"],))
            self.run_task("intros")
            st, d = self.admin.call("GET", f"/api/items/{eps[0]['id']}")
            self.assertEqual((d["markers"]["intro"]["start"], d["markers"]["intro"]["source"]), (1000, "manual"))
        finally:
            self.admin.call("DELETE", f"/api/admin/libraries/{lib['id']}")

    # ------------------------------------------------------------ TV en direct
    def test_live_dead_channels_hidden(self):
        import http.server
        import socketserver

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/ok.m3u8":
                    body = b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def log_message(self, *a):
                pass
        srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        m3u = os.path.join(self.work, "verif.m3u")
        with open(m3u, "w", encoding="utf-8") as f:
            f.write(f"#EXTM3U\n#EXTINF:-1,Bonne\nhttp://127.0.0.1:{port}/ok.m3u8\n"
                    f"#EXTINF:-1,Morte\nhttp://127.0.0.1:{port}/absente.m3u8\n"
                    f"#EXTINF:-1,Fermee\nhttp://127.0.0.1:1/x.m3u8\n")
        st, r = self.admin.call("POST", "/api/admin/live/sources", {"name": "Vérif", "m3u_url": m3u})
        self.assertEqual(st, 200, r)
        sid = next(x["id"] for x in r["sources"] if x["name"] == "Vérif")
        try:
            def names():
                st, live = self.lea.call("GET", "/api/live")
                return sorted(c["name"] for c in live["channels"] if c["name"] in ("Bonne", "Morte", "Fermee"))
            time.sleep(1.5)  # première vérification lancée en fond après l'ajout
            self.ctx.live.check_channels(int(sid))  # deux échecs d'affilée : masquées
            self.assertEqual(names(), ["Bonne"])
            st, r = self.admin.call("GET", "/api/admin/live")
            self.assertEqual(next(x for x in r["sources"] if x["id"] == sid)["dead"], 2)
            # une chaîne qui remarche réapparaît
            self.ctx.db.run("UPDATE live_channels SET url=? WHERE name='Morte'", (f"http://127.0.0.1:{port}/ok.m3u8",))
            self.ctx.live.check_channels(int(sid))
            self.assertEqual(names(), ["Bonne", "Morte"])
        finally:
            self.admin.call("DELETE", f"/api/admin/live/sources/{sid}")
            srv.shutdown()

    def test_live_tv(self):
        films = os.path.join(self.media, "films")
        mp4 = os.path.join(films, "Le Grand Voyage (2019)", "Le Grand Voyage (2019).mp4")
        hevc = os.path.join(films, "Brume (2022)", "Brume.2022.HEVC.mkv")
        m3u = os.path.join(self.work, "chaines.m3u")
        epg = os.path.join(self.work, "guide.xml")
        now = time.time()

        def t(sec):
            return time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(sec))
        with open(epg, "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?><tv>'
                    '<channel id="voyage.fr"><display-name>Voyage TV</display-name></channel>'
                    '<channel id="brume.fr"><display-name>Brume HD</display-name></channel>'
                    f'<programme start="{t(now - 600)}" stop="{t(now + 1200)}" channel="voyage.fr"><title>Le journal</title>'
                    '<desc>Les nouvelles du jour.</desc><category>Info</category></programme>'
                    f'<programme start="{t(now + 1200)}" stop="{t(now + 4800)}" channel="voyage.fr"><title>Le film du soir</title></programme>'
                    f'<programme start="{t(now - 60)}" stop="{t(now + 3600)}" channel="Brume HD"><title>Documentaire</title></programme>'
                    '</tv>')
        with open(m3u, "w", encoding="utf-8") as f:
            f.write(f'#EXTM3U url-tvg="{epg}"\n'
                    f'#EXTINF:-1 tvg-id="voyage.fr" tvg-chno="1" tvg-logo="http://127.0.0.1:1/x.png" group-title="Généralistes",Voyage TV\n{mp4}\n'
                    f'#EXTINF:-1 tvg-name="Brume HD" group-title="Cinéma",Brume HD\n#EXTVLCOPT:http-user-agent=Kodi\n{hevc}\n'
                    f'#EXTINF:-1 group-title="Adultes",Chaîne masquée\n{mp4}\n')
        self.assertEqual(self.lea.call("POST", "/api/admin/live/sources", {"name": "x", "m3u_url": m3u})[0], 403)
        self.assertEqual(self.admin.call("POST", "/api/admin/live/sources", {"name": "x", "m3u_url": "ftp://a"})[0], 400)
        st, r = self.admin.call("POST", "/api/admin/live/sources", {"name": "Ma box", "m3u_url": m3u})
        self.assertEqual(st, 200, r)
        src = r["sources"][0]
        self.assertEqual((src["channels"], src["programmes"], src["error"]), (3, 3, None), src)
        sid = src["id"]
        try:
            st, groups = self.admin.call("GET", f"/api/admin/live/sources/{sid}/groups")
            self.assertEqual([g["name"] for g in groups], ["Généralistes", "Cinéma", "Adultes"])
            self.admin.call("PUT", f"/api/admin/live/sources/{sid}/groups", {"hidden": ["Adultes"]})
            st, me = self.lea.call("GET", "/api/me")
            self.assertTrue(me["features"]["live"])
            st, live = self.lea.call("GET", "/api/live")
            self.assertEqual(st, 200, live)
            self.assertEqual([c["name"] for c in live["channels"]], ["Voyage TV", "Brume HD"])
            self.assertEqual(live["groups"], ["Généralistes", "Cinéma"])
            voyage, brume = live["channels"]
            self.assertEqual((voyage["number"], voyage["now"]["title"], voyage["next"]["title"]), (1, "Le journal", "Le film du soir"))
            self.assertEqual(brume["now"]["title"], "Documentaire")  # guide relié par le nom de la chaîne
            self.assertEqual(self.lea.call("GET", f"/api/live/logo/{voyage['id']}")[0], 404)  # logo injoignable
            # favoris (par personne) et guide
            self.lea.call("POST", f"/api/live/{brume['id']}/favorite", {"on": True})
            st, g = self.lea.call("GET", "/api/live/guide?group=__fav__&hours=3")
            self.assertEqual([c["name"] for c in g["channels"]], ["Brume HD"])
            self.assertFalse(self.admin.call("GET", "/api/live")[1]["channels"][1]["favorite"])
            st, g = self.lea.call("GET", "/api/live/guide?hours=6")
            self.assertEqual([p["title"] for p in g["channels"][0]["programmes"]], ["Le journal", "Le film du soir"])
            # lecture : flux HLS (MPEG-TS) fabriqué par ffmpeg, partagé entre spectateurs
            st, p = self.lea.call("POST", f"/api/live/{voyage['id']}/play")
            self.assertEqual((st, p["mode"]), (200, "copy"), p)
            st, pl, h = self.lea.call("GET", p["url"], raw=True)
            self.assertEqual(st, 200, pl)
            seg = [l for l in pl.decode().splitlines() if l.endswith(".ts")][0]
            st, ts, h = self.lea.call("GET", p["url"].rsplit("/", 1)[0] + "/" + seg, raw=True)
            self.assertEqual((st, ts[:1], h["Content-Type"]), (200, b"\x47", "video/mp2t"))
            st, p2 = self.admin.call("POST", f"/api/live/{voyage['id']}/play")
            self.assertEqual(p2["session"], p["session"])
            self.assertEqual(self.admin.call("GET", "/api/admin/live")[1]["sessions"][0]["viewers"], ["Léa", "colas"])
            # limite de chaînes simultanées
            self.admin.call("PUT", "/api/admin/live/settings", {"max_sessions": 1})
            st, err = self.lea.call("POST", f"/api/live/{brume['id']}/play")
            self.assertEqual(st, 503, err)
            self.assertIn("maximum", err["error"])
            self.admin.call("PUT", "/api/admin/live/settings", {"max_sessions": 3})
            st, p3 = self.lea.call("POST", f"/api/live/{brume['id']}/play")
            self.assertEqual((st, p3["mode"]), (200, "transcode"), p3)  # HEVC : converti en H.264
            st, pl3, _ = self.lea.call("GET", p3["url"], raw=True)
            self.assertEqual(st, 200, pl3)
            for c in (self.lea, self.admin):
                c.call("POST", f"/api/live/s/{p['session']}/stop")
            self.lea.call("POST", f"/api/live/s/{p3['session']}/stop")
            self.assertEqual(self.lea.call("GET", p["url"])[0], 404)
            # profils avec restriction d'âge : option pour masquer la TV
            self.admin.call("PUT", "/api/admin/live/settings", {"hide_restricted": True})
            self.ctx.db.run("UPDATE users SET max_age=12 WHERE username='lea'")
            self.assertEqual(self.lea.call("GET", "/api/live")[0], 404)
            self.assertFalse(self.lea.call("GET", "/api/me")[1]["features"]["live"])
            self.ctx.db.run("UPDATE users SET max_age=NULL WHERE username='lea'")
            self.admin.call("PUT", "/api/admin/live/settings", {"hide_restricted": False})
            # tâche planifiée d'actualisation
            last = self.run_task("livetv")
            self.assertEqual(last["status"], "ok", last)
            self.assertIn("3 chaîne(s)", last["summary"])
        finally:
            self.ctx.live.stop_all()
            self.admin.call("DELETE", f"/api/admin/live/sources/{sid}")

    # ------------------------------------------------------------ regarder ensemble
    def events(self, client, gid, n, timeout=10, path=None):
        """Lit n événements SSE du groupe (connexion HTTP brute avec le cookie de session)."""
        cookie = "; ".join(f"{c.name}={c.value}" for c in client.jar)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        conn.request("GET", path or f"/api/together/{gid}/events", headers={"Cookie": cookie})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.getheader("Content-Type").startswith("text/event-stream"))
        out, buf = [], b""
        deadline = time.time() + timeout
        while len(out) < n and time.time() < deadline:
            try:
                chunk = resp.fp.readline()
            except (TimeoutError, OSError):
                break
            if not chunk:
                break
            buf += chunk
            for line in chunk.splitlines():
                if line.startswith(b"data: "):
                    out.append(json.loads(line[6:]))
        return conn, out

    def test_watch_together(self):
        film = self.item("Le Grand Voyage")
        st, g = self.admin.call("POST", "/api/together", {"item_id": film["id"], "position": 5000, "name": "Soirée cinéma"})
        self.assertEqual(st, 200, g)
        gid = g["id"]
        self.assertEqual((g["playing"], g["waiting"], g["is_host"]), (True, True, True))  # on attend que l'hôte ait chargé
        st, lst = self.lea.call("GET", "/api/together")
        self.assertEqual([(x["name"], x["item"]["title"], x["member"]) for x in lst], [("Soirée cinéma", "Le Grand Voyage", False)])
        st, j = self.lea.call("POST", f"/api/together/{gid}/join")
        self.assertEqual((st, j["item_id"], j["position"]), (200, str(film["id"]), 5000))
        conn, ev = self.events(self.lea, gid, 2)
        try:
            self.assertEqual([e["type"] for e in ev], ["members", "state"])
            self.assertEqual({m["name"] for m in ev[0]["members"]}, {"colas", "Léa"})
            # l'hôte est prêt : la lecture démarre pour tout le monde
            st, s = self.admin.call("POST", f"/api/together/{gid}/cmd", {"action": "ready"})
            self.assertEqual((s["playing"], s["waiting"]), (True, False), s)
            # Léa met en pause : la position est partagée
            st, s = self.lea.call("POST", f"/api/together/{gid}/cmd", {"action": "pause", "position": 12000})
            self.assertEqual((s["playing"], s["position"]), (False, 12000))
            # un saut pendant la lecture : on attend que chacun ait rechargé
            self.lea.call("POST", f"/api/together/{gid}/cmd", {"action": "play", "position": 12000})
            st, s = self.lea.call("POST", f"/api/together/{gid}/cmd", {"action": "seek", "position": 60000})
            self.assertEqual((s["waiting"], s["waiting_for"]), (True, ["colas"]), s)
            st, s = self.admin.call("POST", f"/api/together/{gid}/cmd", {"action": "ready"})
            self.assertFalse(s["waiting"])
            # coupure réseau chez quelqu'un : tout le groupe attend
            st, s = self.admin.call("POST", f"/api/together/{gid}/cmd", {"action": "buffering"})
            self.assertEqual((s["waiting"], s["waiting_for"]), (True, ["colas"]))
            self.admin.call("POST", f"/api/together/{gid}/cmd", {"action": "ready"})
            # Léa change de langue : tout le monde attend à sa position, plus longtemps qu'une simple coupure
            st, s = self.lea.call("POST", f"/api/together/{gid}/cmd",
                                  {"action": "buffering", "position": 70000, "reason": "reload"})
            self.assertEqual((s["waiting"], s["waiting_for"], s["position"]), (True, ["Léa"], 70000), s)
            g = self.ctx.together.get(gid)
            self.assertEqual(g.wait_timer.interval, 60)
            st, s = self.lea.call("POST", f"/api/together/{gid}/cmd", {"action": "ready"})
            self.assertEqual((s["waiting"], s["playing"]), (False, True))
            # changement d'épisode / de film
            other = self.item("Nuit polaire")
            st, s = self.admin.call("POST", f"/api/together/{gid}/cmd", {"action": "item", "item_id": int(other["id"])})
            self.assertEqual((s["item_id"], s["position"], s["waiting"]), (str(other["id"]), 0, True))
            # seul l'hôte contrôle
            st, v = self.admin.call("PUT", f"/api/together/{gid}", {"host_only": True})
            self.assertTrue(v["host_only"])
            self.assertEqual(self.lea.call("POST", f"/api/together/{gid}/cmd", {"action": "pause"})[0], 403)
            self.assertEqual(self.lea.call("PUT", f"/api/together/{gid}", {"host_only": False})[0], 403)
            self.assertEqual(self.lea.call("POST", f"/api/together/{gid}/cmd", {"action": "ready"})[0], 200)
            self.assertEqual(self.lea.call("POST", f"/api/together/{gid}/cmd", {"action": "dance"})[0], 400)
        finally:
            conn.close()
        # l'hôte s'en va : Léa devient l'hôte
        self.admin.call("POST", f"/api/together/{gid}/leave")
        st, v = self.lea.call("GET", f"/api/together/{gid}")
        self.assertEqual((v["owner"], v["is_host"]), ("Léa", True))
        self.lea.call("POST", f"/api/together/{gid}/leave")
        self.assertEqual(self.lea.call("POST", f"/api/together/{gid}/cmd", {"action": "play"})[0], 403)
        self.assertEqual(self.lea.call("GET", "/api/together/zzzzzzzz")[0], 404)

    def test_watch_together_events_stream(self):
        film = self.item("Le Grand Voyage")
        st, g = self.admin.call("POST", "/api/together", {"item_id": film["id"]})
        gid = g["id"]
        self.lea.call("POST", f"/api/together/{gid}/join")
        got = []

        def listen():
            conn, ev = self.events(self.lea, gid, 5, timeout=8)
            got.extend(ev)
            conn.close()
        t = threading.Thread(target=listen)
        t.start()
        time.sleep(0.5)
        self.admin.call("POST", f"/api/together/{gid}/cmd", {"action": "ready"})
        self.admin.call("POST", f"/api/together/{gid}/cmd", {"action": "pause", "position": 3000})
        t.join(10)
        states = [e for e in got if e["type"] == "state"]
        self.assertEqual([s["action"] for s in states], ["sync", "resume", "pause"], got)
        self.assertEqual((states[-1]["by"], states[-1]["position"]), ("colas", 3000))
        self.admin.call("POST", f"/api/together/{gid}/leave")
        self.lea.call("POST", f"/api/together/{gid}/leave")

    def test_watch_together_live_list_and_lobby(self):
        """Liste des groupes en direct, groupe créé à l'avance (salon), présence tant que Nexis est ouvert."""
        from seance import syncplay
        got = []

        def listen():
            conn, ev = self.events(self.lea, None, 4, timeout=8, path="/api/together/feed")
            got.extend(ev)
            conn.close()
        t = threading.Thread(target=listen)
        t.start()
        time.sleep(0.5)
        # salon : groupe sans film, créé à l'avance
        st, g = self.admin.call("POST", "/api/together", {"name": "Vendredi soir"})
        self.assertEqual((st, g["item"], g["playing"]), (200, None, False))
        gid = g["id"]
        time.sleep(0.5)
        self.lea.call("POST", f"/api/together/{gid}/join")
        time.sleep(0.5)
        film = self.item("Le Grand Voyage")
        st, s = self.admin.call("POST", f"/api/together/{gid}/cmd", {"action": "item", "item_id": int(film["id"])})
        self.assertEqual((s["item_id"], s["waiting"]), (str(film["id"]), True))
        t.join(10)
        lists = [e["groups"] for e in got if e["type"] == "groups"]
        self.assertEqual(lists[0], [])  # rien au départ
        self.assertEqual([(x["name"], x["item"], x["member"], x["is_host"]) for x in lists[1]],
                         [("Vendredi soir", None, False, False)])  # créé : apparaît chez Léa sans recharger
        self.assertTrue(lists[2][0]["member"])  # Léa a rejoint
        self.assertEqual(lists[3][0]["item"]["title"], "Le Grand Voyage")  # le film est lancé
        # présence : membre sans lecteur ouvert, mais Nexis ouvert -> on reste dans le groupe
        old = syncplay.GONE_AFTER
        syncplay.GONE_AFTER = 0
        try:
            # (Léa a encore sa liste en direct ouverte ; colas aussi, le temps du test)
            q = self.ctx.together.watch({"id": self.ctx.db.scalar("SELECT id FROM users WHERE username='colas'")})
            time.sleep(0.05)
            self.ctx.together.reap()
            members = {m["name"] for m in self.lea.call("GET", f"/api/together/{gid}")[1]["members"]}
            self.assertEqual(members, {"colas", "Léa"})
            self.ctx.together.unwatch(q)  # colas ferme Nexis
            time.sleep(0.05)
            self.ctx.together.reap()
            members = {m["name"] for m in self.lea.call("GET", f"/api/together/{gid}")[1]["members"]}
            self.assertEqual(members, {"Léa"})
            # salon oublié sur un écran resté allumé : la présence ne garde pas membre indéfiniment
            g = self.ctx.together.get(gid)
            g.members[next(iter(g.members))].seen -= syncplay.PRESENT_FOR + 1
            self.ctx.together.reap()
            self.assertEqual(g.members, {})
        finally:
            syncplay.GONE_AFTER = old
        self.lea.call("POST", f"/api/together/{gid}/leave")

    def test_watch_together_feed_ends_with_session(self):
        """Déconnexion (ou compte supprimé) : la liste en direct s'arrête."""
        from seance import syncplay
        c = Client(self.base)
        c.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})
        old, syncplay.FEED_PING = syncplay.FEED_PING, 0.5
        try:
            cookie = "; ".join(f"{k.name}={k.value}" for k in c.jar)
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.request("GET", "/api/together/feed", headers={"Cookie": cookie})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            c.call("POST", "/api/logout")
            t0 = time.time()
            resp.read()  # se termine quand le serveur coupe le flux
            self.assertLess(time.time() - t0, 5)
            conn.close()
        finally:
            syncplay.FEED_PING = old


if __name__ == "__main__":
    unittest.main()
