"""Serveurs liés : jumelage chiffré, tunnel, comptes répliqués, catalogue partagé, lecture relayée.

Deux serveurs complets dans le même processus : A (films) et B (séries). B rejoint A sans donner d'adresse :
A ne peut donc lui parler que par le tunnel que B lui ouvre.

python3 -m unittest tests.test_federation
"""
import http.client
import json
import os
import shutil
import ssl
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
        cls.media = os.path.join(cls.work, "media")
        shutil.copytree(root, cls.media)
        cls.a, cls.sa, cls.base_a = start_server(cls.work, "a")
        cls.b, cls.sb, cls.base_b = start_server(cls.work, "b")
        cls.admin_a, cls.admin_b = Client(cls.base_a), Client(cls.base_b)
        for ctx, client, pw in ((cls.a, cls.admin_a, "motdepasseA"), (cls.b, cls.admin_b, "motdepasseB")):
            st, r = client.call("POST", "/api/setup", {"username": "colas", "password": pw},
                                headers={"X-Setup-Code": ctx.setup_code})
            assert st == 200, r
        st, r = cls.admin_a.call("POST", "/api/admin/libraries", {"name": "Films", "kind": "movie",
                                                                    "paths": [os.path.join(cls.media, "films")]})
        assert st == 200, r
        wait_scan(cls.admin_a)
        st, r = cls.admin_b.call("POST", "/api/admin/libraries", {"name": "Séries", "kind": "show",
                                                                    "paths": [os.path.join(cls.media, "series")]})
        assert st == 200, r
        wait_scan(cls.admin_b)
        st, r = cls.admin_b.call("POST", "/api/admin/users", {"username": "lea", "password": "motdepasseL"})
        assert st == 200, r
        # invitation créée sur A (joignable), collée sur B (sans adresse : derrière une box, par exemple)
        cls.a.fed.listen()
        st, inv = cls.admin_a.call("POST", "/api/admin/federation/invite", {"address": f"127.0.0.1:{cls.a.fed.port}"})
        assert st == 200, inv
        cls.invite = inv["invite"]
        st, cls.joined = cls.admin_b.call("POST", "/api/admin/federation/join", {"invite": inv["invite"]})
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

    def test_03_catalog_shared(self):
        st, libs = self.admin_a.call("GET", "/api/libraries")
        titles = {l["title"]: l for l in libs}
        name_b = self.b.fed.name()
        self.assertIn("Films", titles)
        self.assertIn(f"Séries · {name_b}", titles)
        remote = titles[f"Séries · {name_b}"]
        st, items = self.admin_a.call("GET", f"/api/libraries/{remote['id']}/items")
        self.assertEqual(st, 200)
        self.assertEqual([i["title"] for i in items["items"]], ["Les Veilleurs"])
        show = items["items"][0]
        st, d = self.admin_a.call("GET", f"/api/items/{show['id']}")
        self.assertEqual(st, 200, d)
        self.assertTrue(d["seasons"])
        # la page d'administration des bibliothèques ne montre que celles de ce serveur
        st, admin_libs = self.admin_a.call("GET", "/api/admin/libraries")
        self.assertEqual([l["name"] for l in admin_libs["libraries"]], ["Films"])
        st, r = self.admin_a.call("DELETE", f"/api/admin/libraries/{remote['id']}")
        self.assertEqual(st, 404)
        # recherche : les deux serveurs
        st, res = self.admin_a.call("GET", "/api/search?q=veilleurs")
        self.assertTrue(any(i["title"] == "Les Veilleurs" for v in res.values() if isinstance(v, list) for i in v), res)
        # affiche relayée (image tirée de la vidéo par B)
        st, img, h = self.admin_a.call("GET", f"/api/img?p=/library/metadata/{show['id']}/thumb/1&w=200&h=300", raw=True)
        self.assertEqual(st, 200, img[:200])
        self.assertEqual(h.get("Content-Type"), "image/jpeg")
        self.assertTrue(img.startswith(b"\xff\xd8"), img[:40])  # une vraie image JPEG, pas une page
        # et dans l'autre sens
        st, libs_b = self.admin_b.call("GET", "/api/libraries")
        self.assertIn(f"Films · {self.a.fed.name()}", [l["title"] for l in libs_b])

    def remote_episode(self):
        rk = self.a.db.scalar("SELECT id FROM items WHERE peer_id IS NOT NULL AND type='episode' ORDER BY id")
        remote = self.a.db.scalar("SELECT remote_id FROM items WHERE id=?", (rk,))
        return str(rk), remote

    def test_04_play_from_other_server(self):
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
        # la lecture apparaît sur les deux serveurs
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
        # pas de téléchargement hors ligne pour l'instant
        st, r = self.admin_a.call("POST", "/api/downloads/options", {"id": rk})
        self.assertEqual(st, 400)

    def test_05_progress_comes_back(self):
        rk, remote = self.remote_episode()
        colas_b = self.login(self.base_b, "colas", "motdepasseA")
        st, r = colas_b.call("POST", f"/api/items/{remote}/watched", {"watched": True})
        self.assertEqual(st, 200, r)
        self.sync()
        st, d = self.admin_a.call("GET", f"/api/items/{rk}")
        self.assertTrue(d["watched"], d)

    def test_06_link_is_protected(self):
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

    def test_99_unlink(self):
        st, r = self.admin_b.call("DELETE", f"/api/admin/federation/peers/{self.peer_on_b}")
        self.assertEqual(st, 200, r)
        self.assertEqual(r["peers"], [])
        st, va = self.admin_a.call("GET", "/api/admin/federation")
        self.assertEqual(va["peers"], [])
        st, libs = self.admin_a.call("GET", "/api/libraries")
        self.assertEqual([l["title"] for l in libs], ["Films"])
        # les comptes restent
        self.login(self.base_a, "lea", "motdepasseL")


if __name__ == "__main__":
    unittest.main()
