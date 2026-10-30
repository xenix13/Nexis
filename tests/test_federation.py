"""Serveurs liés : jumelage chiffré, tunnel, comptes répliqués, catalogue partagé, fusion, lecture relayée.

Deux serveurs complets dans le même processus. B rejoint A sans donner d'adresse : A ne peut donc lui parler
que par le tunnel que B lui ouvre.
- A : Films (Brume en français 180p, Le Grand Voyage, Nuit Polaire), Séries (Les Veilleurs S01E01 seulement)
- B : Films (Brume en anglais 360p, Ciel Bas), Séries (Les Veilleurs complète), Animes

python3 -m unittest tests.test_federation
"""
import http.client
import json
import re
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

from tests.test_api import STATIC, Client, media_root, wait_scan

from seance.api import Ctx, build_app
from seance.web import Server, make_handler


def start_server(work, name):
    ctx = Ctx(os.path.join(work, name), STATIC)
    ctx.fed.port = 0
    server = Server(("127.0.0.1", 0), make_handler(build_app(ctx)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ctx.fed.start(loop=False)
    return ctx, server, f"http://127.0.0.1:{server.server_address[1]}"


def fed_raw(port, method, path, headers=None, body=None):
    """Requête directe sur le port des serveurs liés (TLS, sans vérifier le certificat)."""
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=c, timeout=20)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


class FederationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        root = media_root()
        cls.media = m = os.path.join(cls.work, "media")
        brume = os.path.join(root, "films", "Brume (2022)", "Brume.2022.HEVC.mkv")
        shutil.copytree(os.path.join(root, "films"), os.path.join(m, "films_a"))
        os.remove(os.path.join(m, "films_a", "Brume (2022)", "Brume.2022.HEVC.mkv"))
        ff = ["ffmpeg", "-v", "error", "-y", "-i", brume]
        subprocess.run([*ff, "-map", "0", "-c", "copy", "-metadata:s:a:0", "language=fre",
                        os.path.join(m, "films_a", "Brume (2022)", "Brume.2022.HEVC.mkv")], check=True)
        os.makedirs(os.path.join(m, "films_b", "Brume (2022)"))
        subprocess.run([*ff, "-map", "0:v", "-map", "0:a", "-vf", "scale=640:360", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "copy", "-metadata:s:a:0", "language=eng",
                        os.path.join(m, "films_b", "Brume (2022)", "Brume.2022.mkv")], check=True)
        os.makedirs(os.path.join(m, "films_b", "Ciel Bas (2020)"))
        shutil.copy(os.path.join(root, "films", "Nuit.Polaire.2021.1080p.BluRay.x264.mkv"),
                    os.path.join(m, "films_b", "Ciel Bas (2020)", "Ciel.Bas.2020.mkv"))
        shutil.copytree(os.path.join(root, "series"), os.path.join(m, "series_b"))
        ep1 = os.path.join("Les Veilleurs", "Season 01", "Les.Veilleurs.S01E01.720p.WEB.mkv")
        os.makedirs(os.path.dirname(os.path.join(m, "series_a", ep1)))
        shutil.copy(os.path.join(root, "series", ep1), os.path.join(m, "series_a", ep1))
        shutil.copytree(os.path.join(root, "animes"), os.path.join(m, "animes_b"))
        old = time.time() - 3600  # sinon le scan les croit en cours de copie et les remet à plus tard
        for d, _, files in os.walk(m):
            for f in files:
                os.utime(os.path.join(d, f), (old, old))
        cls.a, cls.sa, cls.base_a = start_server(cls.work, "a")
        cls.b, cls.sb, cls.base_b = start_server(cls.work, "b")
        cls.admin_a, cls.admin_b = Client(cls.base_a), Client(cls.base_b)
        for ctx, client, pw in ((cls.a, cls.admin_a, "motdepasseA"), (cls.b, cls.admin_b, "motdepasseB")):
            st, r = client.call("POST", "/api/setup", {"username": "colas", "password": pw},
                                headers={"X-Setup-Code": ctx.setup_code})
            assert st == 200, r
        for client, name, kind, folder in ((cls.admin_a, "Films", "movie", "films_a"), (cls.admin_a, "Séries", "show", "series_a"),
                                           (cls.admin_b, "Films", "movie", "films_b"), (cls.admin_b, "Séries", "show", "series_b"),
                                           (cls.admin_b, "Animes", "show", "animes_b")):
            st, r = client.call("POST", "/api/admin/libraries", {"name": name, "kind": kind, "paths": [os.path.join(m, folder)]})
            assert st == 200, r
            wait_scan(client)
        st, r = cls.admin_b.call("POST", "/api/admin/users", {"username": "lea", "password": "motdepasseL"})
        assert st == 200, r
        # « papa » existe des deux côtés mais ce n'est pas la même personne
        for client, pw in ((cls.admin_a, "motdepassePA"), (cls.admin_b, "motdepassePB")):
            st, r = client.call("POST", "/api/admin/users", {"username": "papa", "password": pw})
            assert st == 200, r
        # invitation créée sur A (joignable), collée sur B (sans adresse : derrière une box, par exemple)
        cls.a.fed.listen()
        st, inv = cls.admin_a.call("POST", "/api/admin/federation/invite", {"address": f"127.0.0.1:{cls.a.fed.port}"})
        assert st == 200, inv
        cls.invite = inv["invite"]
        st, cls.preview = cls.admin_b.call("POST", "/api/admin/federation/join/preview", {"invite": inv["invite"]})
        assert st == 200, cls.preview
        # colas : la même personne ; papa : par défaut, deux personnes
        st, cls.joined = cls.admin_b.call("POST", "/api/admin/federation/join",
                                          {"invite": inv["invite"], "accounts": {"colas": {"merge": True}}})
        assert st == 200, cls.joined
        cls.peer_on_a = cls.a.db.scalar("SELECT id FROM peers")
        cls.peer_on_b = cls.b.db.scalar("SELECT id FROM peers")
        cls.sync()

    @classmethod
    def tearDownClass(cls):
        for ctx, server in ((cls.a, cls.sa), (cls.b, cls.sb)):
            ctx.fed.stop()
            server.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    @classmethod
    def sync(cls):
        for _ in range(2):  # dans les deux sens, deux fois : ce qui revient de l'autre est appliqué aussi
            cls.a.fed.sync(cls.peer_on_a)
            cls.b.fed.sync(cls.peer_on_b)

    def login(self, base, username, password):
        c = Client(base)
        st, r = c.call("POST", "/api/login", {"username": username, "password": password})
        self.assertEqual(st, 200, r)
        return c

    # ---------------------------------------------------------------- jumelage
    def test_01_paired(self):
        st, va = self.admin_a.call("GET", "/api/admin/federation")
        self.assertEqual(st, 200)
        self.assertEqual(len(va["peers"]), 1)
        pb = va["peers"][0]
        self.assertTrue(pb["online"], pb)
        self.assertIsNone(pb["address"])          # B n'est pas joignable : A passe par le tunnel
        self.assertEqual(pb["via"], "tunnel")
        st, vb = self.admin_b.call("GET", "/api/admin/federation")
        self.assertEqual(vb["peers"][0]["via"], "direct")
        self.assertEqual(vb["peers"][0]["fingerprint"], va["fingerprint"])
        # l'invitation ne sert qu'une fois
        st, r = self.admin_b.call("POST", "/api/admin/federation/join", {"invite": self.invite})
        self.assertEqual(st, 409, r)

    def test_02a_same_username_different_people(self):
        self.assertEqual([c["username"] for c in self.preview["conflicts"]], ["colas", "papa"])
        suggested = self.preview["conflicts"][1]["suggested"]
        self.assertEqual(suggested, "papa-" + re.sub(r"[^\w.-]+", "", self.b.fed.name().lower())[:12])
        # le papa de A garde son identifiant, celui de B est renommé, chacun avec son mot de passe, sur les deux serveurs
        for base in (self.base_a, self.base_b):
            self.login(base, "papa", "motdepassePA")
            self.login(base, suggested, "motdepassePB")
            st, _ = Client(base).call("POST", "/api/login", {"username": "papa", "password": "motdepassePB"})
            self.assertEqual(st, 401)

    def test_02_accounts_replicated(self):
        # même identifiant des deux côtés : le compte de A l'emporte (mot de passe de A partout)
        self.login(self.base_b, "colas", "motdepasseA")
        st, _ = Client(self.base_b).call("POST", "/api/login", {"username": "colas", "password": "motdepasseB"})
        self.assertEqual(st, 401)
        # compte qui n'existait que sur B : présent sur A
        self.login(self.base_a, "lea", "motdepasseL")
        # créé sur B après le jumelage -> sur A ; renommé sur A -> sur B ; supprimé sur A -> plus sur B
        st, tom = self.admin_b.call("POST", "/api/admin/users", {"username": "tom", "password": "motdepasseT"})
        self.assertEqual(st, 200, tom)
        self.sync()
        tom_a = self.login(self.base_a, "tom", "motdepasseT")
        st, me = tom_a.call("GET", "/api/me")
        uid_a = me["user"]["id"]
        st, r = self.admin_a.call("PUT", f"/api/admin/users/{uid_a}", {"display_name": "Thomas"})
        self.assertEqual(st, 200, r)
        self.sync()
        st, users = self.admin_b.call("GET", "/api/admin/users")
        self.assertEqual(next(u for u in users if u["username"] == "tom")["display_name"], "Thomas")
        st, r = self.admin_a.call("DELETE", f"/api/admin/users/{uid_a}")
        self.assertEqual(st, 200, r)
        self.sync()
        st, users = self.admin_b.call("GET", "/api/admin/users")
        self.assertNotIn("tom", [u["username"] for u in users])

    def lib(self, client, name, server=False):
        st, libs = client.call("GET", "/api/libraries")
        return next((l for l in libs if l["title"] == name and bool(l["server"]) == server), None)

    def titles(self, client, lib):
        st, items = client.call("GET", f"/api/libraries/{lib['id']}/items")
        return sorted(i["title"] for i in items["items"])

    def test_03_catalog_shared(self):
        # sans réglage : les bibliothèques de B s'ajoutent, sous leur nom, avec le nom du serveur pour le badge
        st, libs = self.admin_a.call("GET", "/api/libraries")
        name_b = self.b.fed.name()
        self.assertEqual(sorted((l["title"], l["server"] or "") for l in libs),
                         sorted([("Films", ""), ("Séries", ""), ("Films", name_b), ("Séries", name_b), ("Animes", name_b)]))
        remote = self.lib(self.admin_a, "Séries", server=True)
        st, items = self.admin_a.call("GET", f"/api/libraries/{remote['id']}/items")
        self.assertEqual([i["title"] for i in items["items"]], ["Les Veilleurs"])
        show = items["items"][0]
        st, d = self.admin_a.call("GET", f"/api/items/{show['id']}")
        self.assertEqual(st, 200, d)
        self.assertEqual(len(d["seasons"]), 2)
        # la page d'administration des bibliothèques ne montre que celles de ce serveur
        st, admin_libs = self.admin_a.call("GET", "/api/admin/libraries")
        self.assertEqual(sorted(l["name"] for l in admin_libs["libraries"]), ["Films", "Séries"])
        st, r = self.admin_a.call("DELETE", f"/api/admin/libraries/{remote['id']}")
        self.assertEqual(st, 404)
        # affiche relayée (image tirée de la vidéo par B)
        st, img, h = self.admin_a.call("GET", f"/api/img?p=/library/metadata/{show['id']}/thumb/1&w=200&h=300", raw=True)
        self.assertEqual(st, 200, img[:200])
        self.assertEqual(h.get("Content-Type"), "image/jpeg")
        self.assertTrue(img.startswith(b"\xff\xd8"), img[:40])  # une vraie image JPEG, pas une page

    def test_04_merge(self):
        st, v = self.admin_a.call("GET", "/api/admin/federation")
        received = {l["name"]: l for l in v["peers"][0]["libraries"]}
        local = {l["name"]: l for l in v["local_libraries"]}
        # films et séries ne se fusionnent pas entre eux
        st, r = self.admin_a.call("PUT", f"/api/admin/federation/libraries/{received['Films']['id']}",
                                  {"merge_into": local["Séries"]["id"]})
        self.assertEqual(st, 400, r)
        for name in ("Films", "Séries"):
            st, v = self.admin_a.call("PUT", f"/api/admin/federation/libraries/{received[name]['id']}",
                                      {"merge_into": local[name]["id"]})
            self.assertEqual(st, 200, v)
        merged = {l["name"]: l for l in v["peers"][0]["libraries"]}
        self.assertEqual((merged["Films"]["merge_into"], merged["Films"]["merged"]), (local["Films"]["id"], 1))
        # plus qu'une bibliothèque Films et une Séries ; Animes reste à part
        st, libs = self.admin_a.call("GET", "/api/libraries")
        self.assertEqual(sorted(l["title"] for l in libs), ["Animes", "Films", "Séries"])
        films = self.lib(self.admin_a, "Films")
        self.assertEqual(self.titles(self.admin_a, films), ["Brume", "Ciel Bas", "Le Grand Voyage", "Nuit Polaire"])
        series = self.lib(self.admin_a, "Séries")
        self.assertEqual(self.titles(self.admin_a, series), ["Les Veilleurs"])
        show = self.a.db.scalar("SELECT id FROM items WHERE type='show' AND peer_id IS NULL")
        st, d = self.admin_a.call("GET", f"/api/items/{show}")
        self.assertEqual([s["title"] for s in d["seasons"]], ["Saison 1", "Saison 2"])
        st, eps = self.admin_a.call("GET", f"/api/items/{d['seasons'][0]['id']}/children")
        self.assertEqual([e["episode"] for e in eps], [1, 2, 3])
        # recherche : une seule fois
        st, res = self.admin_a.call("GET", "/api/search?q=brume")
        self.assertEqual(sum(1 for v in res.values() if isinstance(v, list) for i in v if i["title"] == "Brume"), 1)

    def remote_episode(self):
        rk = self.a.db.scalar("SELECT id FROM items WHERE peer_id IS NOT NULL AND alias_of IS NULL AND type='episode' "
                              "ORDER BY idx, id")
        remote = self.a.db.scalar("SELECT remote_id FROM items WHERE id=?", (rk,))
        return str(rk), remote

    def test_05_play_from_other_server(self):
        rk, remote = self.remote_episode()
        path = self.b.db.scalar("SELECT key FROM items WHERE id=?", (remote,))
        caps = {"video": ["h264", "hevc"], "audio": ["aac", "ac3", "mp3"], "containers": ["mp4", "matroska", "mov"],
                "native": True}
        st, p = self.admin_a.call("POST", "/api/play", {"id": rk, "caps": caps})
        self.assertEqual(st, 200, p)
        self.assertEqual(p["item"]["id"], rk)
        self.assertEqual(p["mode"], "direct")
        st, data, h = self.admin_a.call("GET", p["url"], headers={"Range": "bytes=0-99"}, raw=True)
        self.assertEqual(st, 206)
        with open(path, "rb") as f:
            self.assertEqual(data, f.read(100))
        st, streams = self.admin_a.call("GET", "/api/admin/streams")
        self.assertIn(p["stream"], [s["id"] for s in streams])
        # progression : enregistrée ici et renvoyée au serveur qui a le fichier
        st, r = self.admin_a.call("POST", "/api/progress", {"id": rk, "position": 42000, "duration": 600000,
                                                            "stream": p["stream"]})
        self.assertEqual(st, 200, r)
        colas_b = self.login(self.base_b, "colas", "motdepasseA")
        for _ in range(50):
            st, d = colas_b.call("GET", f"/api/items/{remote}")
            if d.get("position") == 42000:
                break
            time.sleep(0.1)
        self.assertEqual(d.get("position"), 42000, d)
        self.admin_a.call("POST", "/api/stop", {"stream": p["stream"]})
        # flux préparé par B (remux), relayé par A
        st, p = self.admin_a.call("POST", "/api/play", {"id": rk, "no_direct": True})
        self.assertEqual(st, 200, p)
        self.assertTrue(p["url"].endswith("main.m3u8"))
        st, pl, _ = self.admin_a.call("GET", p["url"], raw=True)
        self.assertEqual(st, 200)
        self.assertIn(b"#EXTM3U", pl)
        st, init, _ = self.admin_a.call("GET", p["url"].replace("main.m3u8", "init.mp4"), raw=True)
        self.assertEqual(st, 200)
        self.admin_a.call("POST", "/api/stop", {"stream": p["stream"]})
        # téléchargement hors ligne : préparé par B, relayé par A
        caps = {"video": ["h264", "hevc"], "audio": ["aac"], "containers": ["mp4"], "native": True}
        st, r = self.admin_a.call("POST", "/api/downloads/options", {"id": rk, "caps": caps})
        self.assertEqual(st, 200, r)
        self.assertEqual(len(r["options"]), 4)
        st, job = self.admin_a.call("POST", "/api/downloads", {"id": rk, "quality": "auto", "caps": caps})
        self.assertEqual(st, 200, job)
        self.assertEqual(job["item_id"], rk)
        self.assertIn(job["id"], self.a.fed.dl_jobs)
        for _ in range(300):
            st, job = self.admin_a.call("GET", f"/api/downloads/{job['id']}")
            self.assertEqual(st, 200, job)
            if job["state"] in ("ready", "error"):
                break
            time.sleep(0.2)
        self.assertEqual((job["state"], job["item_id"]), ("ready", rk), job)
        st, data, h = self.admin_a.call("GET", f"/api/downloads/{job['id']}/file", headers={"Range": "bytes=0-99"}, raw=True)
        self.assertEqual((st, len(data)), (206, 100))
        self.assertIn(b"ftyp", data[:16])
        # un autre compte ne voit pas ce téléchargement
        lea = self.login(self.base_a, "lea", "motdepasseL")
        self.assertEqual(lea.call("GET", f"/api/downloads/{job['id']}")[0], 404)
        st, _ = self.admin_a.call("DELETE", f"/api/downloads/{job['id']}")
        self.assertEqual(st, 200)
        self.assertNotIn(job["id"], self.a.fed.dl_jobs)
        for _ in range(50):
            if not self.b.downloads.jobs:
                break
            time.sleep(0.1)
        self.assertEqual(self.b.downloads.jobs, {})  # B a effacé sa préparation

    def test_06_best_copy(self):
        brume = self.a.db.scalar("SELECT id FROM items WHERE title='Brume' AND peer_id IS NULL")
        # la langue voulue n'existe que sur la copie de A (français) : A, malgré la définition plus faible
        st, p = self.admin_a.call("POST", "/api/play", {"id": str(brume), "no_direct": True})
        self.assertEqual(st, 200, p)
        self.assertNotIn(p["stream"], self.a.fed.proxied)
        self.admin_a.call("POST", "/api/stop", {"stream": p["stream"]})
        # langue présente nulle part : la meilleure qualité, la copie 360p de B
        self.admin_a.call("PUT", "/api/me/prefs", {"audio_lang": "deu"})
        st, p = self.admin_a.call("POST", "/api/play", {"id": str(brume), "no_direct": True})
        self.assertEqual(st, 200, p)
        self.assertIn(p["stream"], self.a.fed.proxied)
        self.assertEqual(p["item"]["id"], str(brume))
        # même titre relancé (saut) : même copie
        st, p2 = self.admin_a.call("POST", "/api/play", {"id": str(brume), "no_direct": True, "offset": 2000,
                                                         "replace": p["stream"]})
        self.assertEqual(st, 200, p2)
        self.assertIn(p2["stream"], self.a.fed.proxied)
        self.assertNotIn(p["stream"], self.a.fed.proxied)
        # pendant la lecture, les sous-pages du titre viennent de la copie lue
        st, tp = self.admin_a.call("GET", f"/api/items/{brume}/trickplay")
        self.assertEqual(st, 200, tp)
        self.admin_a.call("POST", "/api/stop", {"stream": p2["stream"]})
        # piste choisie sur la fiche (anglais, présent seulement chez B) : B
        self.admin_a.call("PUT", "/api/me/prefs", {"audio_lang": "fra"})
        st, p = self.admin_a.call("POST", "/api/play", {"id": str(brume), "no_direct": True})
        self.assertNotIn(p["stream"], self.a.fed.proxied)
        self.admin_a.call("POST", "/api/stop", {"stream": p["stream"]})
        self.admin_a.call("PUT", f"/api/items/{brume}/tracks", {"audio_lang": "eng"})
        st, p = self.admin_a.call("POST", "/api/play", {"id": str(brume), "no_direct": True})
        self.assertIn(p["stream"], self.a.fed.proxied)
        self.assertEqual(p["audio"][0]["lang"], "eng")
        self.admin_a.call("POST", "/api/stop", {"stream": p["stream"]})
        self.admin_a.call("PUT", f"/api/items/{brume}/tracks", {"reset": True})

    def test_07_progress_comes_back(self):
        rk, remote = self.remote_episode()
        colas_b = self.login(self.base_b, "colas", "motdepasseA")
        st, r = colas_b.call("POST", f"/api/items/{remote}/watched", {"watched": True})
        self.assertEqual(st, 200, r)
        # titre présent sur les deux : vu chez B -> vu sur notre copie
        brume_b = self.b.db.scalar("SELECT id FROM items WHERE title='Brume' AND peer_id IS NULL")
        st, r = colas_b.call("POST", f"/api/items/{brume_b}/watched", {"watched": True})
        self.sync()
        st, d = self.admin_a.call("GET", f"/api/items/{rk}")
        self.assertTrue(d["watched"], d)
        brume = self.a.db.scalar("SELECT id FROM items WHERE title='Brume' AND peer_id IS NULL")
        st, d = self.admin_a.call("GET", f"/api/items/{brume}")
        self.assertTrue(d["watched"], d)

    def test_08_offline_server(self):
        fed = self.a.fed
        fed.set_online(self.peer_on_a, False)
        try:
            st, libs = self.admin_a.call("GET", "/api/libraries")
            self.assertEqual(sorted(l["title"] for l in libs), ["Films", "Séries"])  # Animes de B masquée
            films = self.lib(self.admin_a, "Films")
            self.assertEqual(self.titles(self.admin_a, films), ["Brume", "Le Grand Voyage", "Nuit Polaire"])
            show = self.a.db.scalar("SELECT id FROM items WHERE type='show' AND peer_id IS NULL")
            st, d = self.admin_a.call("GET", f"/api/items/{show}")
            self.assertEqual([s["title"] for s in d["seasons"]], ["Saison 1"])
            # le titre présent des deux côtés se lit depuis notre copie, même si B aurait été choisi
            self.admin_a.call("PUT", "/api/me/prefs", {"audio_lang": "deu"})
            brume = self.a.db.scalar("SELECT id FROM items WHERE title='Brume' AND peer_id IS NULL")
            st, p = self.admin_a.call("POST", "/api/play", {"id": str(brume), "no_direct": True})
            self.assertEqual(st, 200, p)
            self.assertNotIn(p["stream"], fed.proxied)
            self.admin_a.call("POST", "/api/stop", {"stream": p["stream"]})
        finally:
            self.admin_a.call("PUT", "/api/me/prefs", {"audio_lang": "fra"})
            fed.set_online(self.peer_on_a, True)
        films = self.lib(self.admin_a, "Films")
        self.assertEqual(len(self.titles(self.admin_a, films)), 4)

    def test_09_local_item_removed(self):
        """Notre épisode supprimé : celui de B reprend sa place au lieu de disparaître avec lui."""
        ep = self.a.db.scalar("SELECT id FROM items WHERE type='episode' AND peer_id IS NULL")
        self.a.db.run("DELETE FROM items WHERE id=?", (ep,))
        self.a.fed.relink(self.peer_on_a)
        self.a.db.release()
        show = self.a.db.scalar("SELECT id FROM items WHERE type='show' AND peer_id IS NULL")
        st, d = self.admin_a.call("GET", f"/api/items/{show}")
        st, eps = self.admin_a.call("GET", f"/api/items/{d['seasons'][0]['id']}/children")
        self.assertEqual([e["episode"] for e in eps], [1, 2, 3])

    def test_10_link_is_protected(self):
        port = self.a.fed.port
        st, _ = fed_raw(port, "GET", "/fed/hello")
        self.assertEqual(st, 401)
        uid_b = self.b.fed.uid()
        st, _ = fed_raw(port, "GET", "/fed/hello", {"X-Fed-Peer": uid_b, "X-Fed-Key": "x" * 43})
        self.assertEqual(st, 401)
        secret = self.a.db.scalar("SELECT secret FROM peers")
        ok = {"X-Fed-Peer": uid_b, "X-Fed-Key": secret}
        st, body = fed_raw(port, "GET", "/fed/hello", ok)
        self.assertEqual(st, 200, body)
        # au nom d'un compte, un serveur lié ne peut que lire : pas d'administration
        colas = self.a.db.scalar("SELECT uid FROM users WHERE username='colas'")
        st, _ = fed_raw(port, "GET", "/api/admin/users", dict(ok, **{"X-Fed-User": colas}))
        self.assertEqual(st, 403)
        # avatar d'un compte : route avec paramètre, servie (et pas la page de l'interface)
        st, body = fed_raw(port, "GET", f"/fed/avatar/{colas}", ok)
        self.assertEqual(st, 404, body[:80])
        # l'interface n'est pas servie sur ce port, et les routes entre serveurs pas sur l'autre
        st, _ = fed_raw(port, "GET", "/", ok)
        self.assertEqual(st, 404)
        st, _ = self.admin_a.call("GET", "/fed/hello")
        self.assertEqual(st, 404)
        # jeton d'invitation inconnu
        st, _ = fed_raw(port, "POST", "/fed/join", {"Content-Type": "application/json"},
                        json.dumps({"token": "z" * 32, "uid": "0" * 32, "fingerprint": "0" * 64}))
        self.assertEqual(st, 403)
        # invitation dont l'empreinte ne correspond pas au serveur contacté
        from seance.federation import decode_invite, encode_invite
        st, inv = self.admin_a.call("POST", "/api/admin/federation/invite", {"address": f"127.0.0.1:{port}"})
        data = decode_invite(inv["invite"])
        data["f"] = "ab" * 32
        data["id"] = "cd" * 16
        st, r = self.admin_b.call("POST", "/api/admin/federation/join", {"invite": encode_invite(data)})
        self.assertEqual(st, 502, r)
        self.assertIn("Certificat inattendu", r["error"])

    def test_11_same_username_created_on_both(self):
        """Le même identifiant créé des deux côtés avant la synchronisation : deux comptes, l'un renommé partout."""
        st, r = self.admin_a.call("POST", "/api/admin/users", {"username": "nina", "password": "motdepasseNA"})
        self.assertEqual(st, 200, r)
        st, r = self.admin_b.call("POST", "/api/admin/users", {"username": "Nina", "password": "motdepasseNB"})
        self.assertEqual(st, 200, r)
        self.sync()
        self.sync()
        q = "SELECT username, uid, password_hash FROM users WHERE username LIKE 'nina%' ORDER BY uid"
        rows_a, rows_b = self.a.db.all(q), self.b.db.all(q)
        self.assertEqual(rows_a, rows_b)
        self.assertEqual(sorted(r["username"].lower() for r in rows_a), ["nina", "nina-2"])
        renamed = next(r["username"] for r in rows_a if r["username"].lower() == "nina-2")
        for base in (self.base_a, self.base_b):  # chacun garde son mot de passe
            logins = {(name, pw): Client(base).call("POST", "/api/login", {"username": name, "password": pw})[0]
                      for name, pw in (("nina", "motdepasseNA"), (renamed, "motdepasseNB"),
                                       ("nina", "motdepasseNB"), (renamed, "motdepasseNA"))}
            self.assertEqual(sorted(logins.values()).count(200), 2, logins)
        st, v = self.admin_a.call("GET", "/api/admin/federation")
        self.assertIn(renamed.lower(), [n["renamed"].lower() for n in v["notices"]])

    def test_12_load_balancing(self):
        """Serveur déjà chargé : la copie d'un serveur libre est lue, même en moins bonne qualité."""
        st, v = self.admin_a.call("PUT", "/api/admin/federation", {"busy": 2})
        self.assertEqual((st, v["busy"]), (200, 2), v)
        self.assertEqual(self.admin_a.call("PUT", "/api/admin/federation", {"busy": 99})[0], 400)
        brume = str(self.a.db.scalar("SELECT id FROM items WHERE title='Brume' AND peer_id IS NULL"))
        st_b = self.a.fed.status.setdefault(self.peer_on_a, {})
        self.admin_a.call("PUT", "/api/me/prefs", {"audio_lang": "deu"})  # aucune copie n'a la langue : la qualité décide
        try:
            st_b["load"] = 5  # B est occupé : notre copie (180p) plutôt que la sienne (360p)
            st, p = self.admin_a.call("POST", "/api/play", {"id": brume, "no_direct": True})
            self.assertEqual(st, 200, p)
            self.assertNotIn(p["stream"], self.a.fed.proxied)
            self.admin_a.call("POST", "/api/stop", {"stream": p["stream"]})
            st_b["load"] = 0  # B libre : sa copie, meilleure
            st, p = self.admin_a.call("POST", "/api/play", {"id": brume, "no_direct": True})
            self.assertIn(p["stream"], self.a.fed.proxied)
            self.assertEqual(st_b["load"], 1)  # compté chez lui d'ici sa prochaine annonce
            self.admin_a.call("POST", "/api/stop", {"stream": p["stream"]})
        finally:
            st_b["load"] = 0
            self.admin_a.call("PUT", "/api/me/prefs", {"audio_lang": "fra"})
            self.admin_a.call("PUT", "/api/admin/federation", {"busy": 3})

    def test_13_watch_together_across_servers(self):
        """Groupe créé sur A, rejoint depuis B : titres, comptes et commandes traduits d'un serveur à l'autre."""
        brume_a = self.a.db.scalar("SELECT id FROM items WHERE title='Brume' AND peer_id IS NULL")
        brume_on_b = str(self.b.db.scalar("SELECT id FROM items WHERE peer_id IS NOT NULL AND remote_id=?", (brume_a,)))
        st, g = self.admin_a.call("POST", "/api/together", {"item_id": str(brume_a), "position": 5000, "name": "Soirée"})
        self.assertEqual(st, 200, g)
        gid = g["id"]
        lea = self.login(self.base_b, "lea", "motdepasseL")
        st, lst = lea.call("GET", "/api/together")
        self.assertEqual(st, 200, lst)
        mine = [x for x in lst if x["id"] == gid]
        self.assertEqual(len(mine), 1, lst)
        self.assertEqual((mine[0]["name"], mine[0]["item"]["id"], mine[0]["member"]), ("Soirée", brume_on_b, False))
        st, j = lea.call("POST", f"/api/together/{gid}/join")
        self.assertEqual(st, 200, j)
        lea_b = self.b.db.scalar("SELECT id FROM users WHERE username='lea'")
        self.assertEqual((j["item_id"], j["item"]["id"], j["position"], j["member"]), (brume_on_b, brume_on_b, 5000, True))
        self.assertIn(lea_b, [m["id"] for m in j["members"]])
        # événements relayés par B
        cookie = "; ".join(f"{c.name}={c.value}" for c in lea.jar)
        conn = http.client.HTTPConnection("127.0.0.1", self.sb.server_address[1], timeout=15)
        conn.request("GET", f"/api/together/{gid}/events", headers={"Cookie": cookie})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)

        def next_event(kind):
            deadline = time.time() + 15
            while time.time() < deadline:
                line = resp.fp.readline()
                if line.startswith(b"data: "):
                    ev = json.loads(line[6:])
                    if ev.get("type") == kind:
                        return ev
            self.fail(f"pas d'événement {kind}")
        members = next_event("members")
        self.assertIn(lea_b, [m["id"] for m in members["members"]])
        state = next_event("state")
        self.assertEqual(state["item_id"], brume_on_b)
        # hôte (A) prêt : on part ; pause depuis B, vue par A
        self.admin_a.call("POST", f"/api/together/{gid}/cmd", {"action": "ready"})
        st, r = lea.call("POST", f"/api/together/{gid}/cmd", {"action": "pause", "position": 7000})
        self.assertEqual((st, r["playing"], r["position"]), (200, False, 7000), r)
        st, ga = self.admin_a.call("GET", f"/api/together/{gid}")
        self.assertEqual((ga["playing"], ga["position"]), (False, 7000))
        # changement de titre depuis B (un titre de B) : A reçoit le sien
        ciel_b = self.b.db.scalar("SELECT id FROM items WHERE title='Ciel Bas' AND peer_id IS NULL")
        ciel_on_a = self.a.db.scalar("SELECT COALESCE(alias_of, id) FROM items WHERE peer_id IS NOT NULL AND remote_id=?",
                                     (ciel_b,))
        st, r = lea.call("POST", f"/api/together/{gid}/cmd", {"action": "item", "item_id": str(ciel_b)})
        self.assertEqual((st, r["item_id"]), (200, str(ciel_b)), r)
        st, ga = self.admin_a.call("GET", f"/api/together/{gid}")
        self.assertEqual(ga["item_id"], str(ciel_on_a))
        ev = next_event("state")
        while ev.get("action") != "item":
            ev = next_event("state")
        self.assertEqual(ev["item_id"], str(ciel_b))
        conn.close()
        st, _ = lea.call("POST", f"/api/together/{gid}/leave")
        self.assertEqual(st, 200)
        st, ga = self.admin_a.call("GET", f"/api/together/{gid}")
        self.assertNotIn("Léa", [m["name"] for m in ga["members"]])
        self.admin_a.call("POST", f"/api/together/{gid}/leave")

    def test_14_shared_tmdb_and_seerr(self):
        """Clé TMDB et Seerr configurés sur A seulement : B s'en sert (métadonnées, Découvrir, demandes)."""
        from tests.fixtures import FakeSeerr
        from tests.test_api import TMDB
        seerr = FakeSeerr()
        try:
            self.assertFalse(self.b.metadata.tmdb().enabled)
            st, r = self.admin_a.call("PUT", "/api/admin/settings", {"tmdb_key": TMDB.key})
            self.assertEqual(st, 200, r)
            st, r = self.admin_a.call("PUT", "/api/admin/requests", {"seerr_url": seerr.url, "seerr_key": seerr.key})
            self.assertEqual(st, 200, r)
            self.sync()
            self.assertTrue(self.b.metadata.tmdb().enabled)
            # titres de B restés sans métadonnées faute de clé : cherchés avec celle de A
            for _ in range(100):
                left = self.b.db.scalar("SELECT COUNT(*) FROM items WHERE peer_id IS NULL AND type IN ('movie','show') "
                                        "AND meta_state IN ('local','pending')")
                if not left:
                    break
                time.sleep(0.1)
            self.assertEqual(left, 0)
            lea = self.login(self.base_b, "lea", "motdepasseL")
            st, me = lea.call("GET", "/api/me")
            self.assertEqual((me["features"]["discover"], me["features"]["requests"]), (True, True))
            st, home = lea.call("GET", "/api/discover/home")
            self.assertEqual((st, home["seerr"]), (200, True), home)
            st, cfg = self.admin_b.call("GET", "/api/admin/requests")
            self.assertEqual((cfg["enabled"], cfg["shared_by"]), (True, self.a.fed.name()))
            st, r = lea.call("POST", "/api/discover/request", {"type": "movie", "tmdb_id": 9101})
            self.assertEqual((st, r["as_user"]), (200, True), r)
            lea_seerr = next(u for u in seerr.users if u.get("username") == "lea")
            self.assertEqual(seerr.requests[-1]["user"], lea_seerr["id"])
            # seules les routes utiles de Seerr sont prêtées
            peer = self.b.fed.peer(self.peer_on_b)
            st, r = self.b.fed.call(peer, "POST", "/fed/seerr", {"method": "GET", "path": "/settings/main"})
            self.assertEqual(st, 403, r)
            st, r = self.b.fed.call(peer, "POST", "/fed/tmdb", {"path": "/../account", "params": {}})
            self.assertEqual(st, 400, r)
        finally:
            self.admin_a.call("PUT", "/api/admin/settings", {"tmdb_key": ""})
            self.admin_a.call("PUT", "/api/admin/requests", {"seerr_url": "", "seerr_key": ""})
            seerr.httpd.shutdown()
            self.sync()

    def test_99_unlink(self):
        st, r = self.admin_b.call("DELETE", f"/api/admin/federation/peers/{self.peer_on_b}")
        self.assertEqual(st, 200, r)
        self.assertEqual(r["peers"], [])
        st, va = self.admin_a.call("GET", "/api/admin/federation")
        self.assertEqual(va["peers"], [])
        st, libs = self.admin_a.call("GET", "/api/libraries")
        self.assertEqual(sorted(l["title"] for l in libs), ["Films", "Séries"])
        films = self.lib(self.admin_a, "Films")
        self.assertEqual(self.titles(self.admin_a, films), ["Brume", "Le Grand Voyage", "Nuit Polaire"])
        # les comptes restent, des deux côtés, et ne bougent plus
        self.login(self.base_a, "lea", "motdepasseL")
        self.login(self.base_b, "colas", "motdepasseA")
        st, libs_b = self.admin_b.call("GET", "/api/libraries")
        self.assertEqual(sorted(l["title"] for l in libs_b), ["Animes", "Films", "Séries"])
        self.assertFalse(self.b.db.scalar("SELECT COUNT(*) FROM items WHERE peer_id IS NOT NULL"))
        self.assertFalse(self.a.db.scalar("SELECT COUNT(*) FROM items WHERE peer_id IS NOT NULL"))
        # notre épisode et notre série sont intacts (les épisodes de B fusionnés ont disparu)
        show = self.a.db.scalar("SELECT id FROM items WHERE type='show'")
        st, d = self.admin_a.call("GET", f"/api/items/{show}")
        self.assertEqual([s["title"] for s in d["seasons"]], ["Saison 1"])

    def pair(self):
        st, inv = self.admin_a.call("POST", "/api/admin/federation/invite", {"address": f"127.0.0.1:{self.a.fed.port}"})
        self.assertEqual(st, 200, inv)
        st, r = self.admin_b.call("POST", "/api/admin/federation/join", {"invite": inv["invite"]})
        self.assertEqual(st, 200, r)
        type(self).peer_on_a = self.a.db.scalar("SELECT id FROM peers")
        type(self).peer_on_b = self.b.db.scalar("SELECT id FROM peers")
        self.sync()

    def test_99b_relink_after_one_sided_unlink(self):
        """Délié d'un seul côté (l'autre était éteint) : un nouveau jumelage remplace l'ancien lien."""
        self.pair()
        self.b.fed.remove(self.peer_on_b, notify=False)  # A ne l'apprend pas
        self.b.db.release()
        self.assertEqual(self.a.db.scalar("SELECT COUNT(*) FROM peers"), 1)
        self.pair()
        self.assertEqual([ctx.db.scalar("SELECT COUNT(*) FROM peers") for ctx in (self.a, self.b)], [1, 1])
        st, v = self.admin_a.call("GET", "/api/admin/federation")
        self.assertTrue(v["peers"][0]["online"], v)
        st, libs = self.admin_a.call("GET", "/api/libraries")
        self.assertEqual(sorted(l["title"] for l in libs), ["Animes", "Films", "Films", "Séries", "Séries"])


if __name__ == "__main__":
    unittest.main()
