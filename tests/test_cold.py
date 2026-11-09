"""Stockage froid : stockages (dossier, WebDAV, S3, SFTP, rclone), archivage vérifié, lecture depuis la copie."""
import base64
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.test_api import STATIC, Client, ffprobe_codec, hls_segment, media_root, wait_scan

from seance import cold as cold_mod
from seance import coldfree
from seance import mountd
from seance.api import Ctx, build_app
from seance.web import Server, make_handler


# ---------------------------------------------------------------------- faux serveurs
class FakeDAV(BaseHTTPRequestHandler):
    files = {}
    dirs = {"/dav"}
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _path(self, url=None):
        return urllib.parse.unquote(urllib.parse.urlsplit(url or self.path).path).rstrip("/") or "/"

    def _auth(self):
        ok = self.headers.get("Authorization") == "Basic " + base64.b64encode(b"nexis:secret").decode()
        if not ok:
            self.reply(401)
        return ok

    def reply(self, status, body=b"", headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_MKCOL(self):
        if not self._auth():
            return
        p = self._path()
        if p in self.dirs:
            return self.reply(405)
        if os.path.dirname(p) not in self.dirs:
            return self.reply(409)
        self.dirs.add(p)
        self.reply(201)

    def do_PUT(self):
        if not self._auth():
            return
        p = self._path()
        data = self._body()
        if os.path.dirname(p) not in self.dirs:
            return self.reply(409)
        self.files[p] = data
        self.reply(201)

    def do_MOVE(self):
        if not self._auth():
            return
        src, dst = self._path(), self._path(self.headers["Destination"])
        if src not in self.files:
            return self.reply(404)
        self.files[dst] = self.files.pop(src)
        self.reply(201)

    def do_PROPFIND(self):
        if not self._auth():
            return
        self._body()
        p = self._path()
        if p in self.files:
            xml = f'<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop><d:getcontentlength>{len(self.files[p])}' \
                  '</d:getcontentlength></d:prop></d:propstat></d:response></d:multistatus>'
            return self.reply(207, xml.encode())
        if p in self.dirs:
            return self.reply(207, b'<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop><d:quota-available-bytes>'
                                   b'123456789</d:quota-available-bytes></d:prop></d:propstat></d:response></d:multistatus>')
        self.reply(404)

    def do_GET(self):
        if not self._auth():
            return
        data = self.files.get(self._path())
        if data is None:
            return self.reply(404)
        m = re.match(r"bytes=(\d+)-(\d+)", self.headers.get("Range") or "")
        if m:
            a, b = int(m.group(1)), min(int(m.group(2)), len(data) - 1)
            return self.reply(206, data[a:b + 1], {"Content-Range": f"bytes {a}-{b}/{len(data)}"})
        self.reply(200, data)

    def do_DELETE(self):
        if not self._auth():
            return
        self.reply(204 if self.files.pop(self._path(), None) is not None else 404)


class FakeS3(BaseHTTPRequestHandler):
    objects = {}
    uploads = {}
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    reply = FakeDAV.reply
    _body = FakeDAV._body

    def _key(self):
        u = urllib.parse.urlsplit(self.path)
        return urllib.parse.unquote(u.path), dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))

    def do_PUT(self):
        key, q = self._key()
        data = self._body()
        if "partNumber" in q:
            self.uploads[q["uploadId"]][int(q["partNumber"])] = data
            return self.reply(200, headers={"ETag": f'"p{q["partNumber"]}"'})
        self.objects[key] = data
        self.reply(200, headers={"ETag": '"x"'})

    def do_POST(self):
        key, q = self._key()
        body = self._body()
        if "uploads" in q:
            uid = f"up{len(self.uploads) + 1}"
            self.uploads[uid] = {}
            return self.reply(200, f"<InitiateMultipartUploadResult><UploadId>{uid}</UploadId></InitiateMultipartUploadResult>".encode())
        parts = self.uploads.pop(q["uploadId"])
        order = [int(n) for n in re.findall(rb"<PartNumber>(\d+)</PartNumber>", body)]
        self.objects[key] = b"".join(parts[n] for n in order)
        self.reply(200, b"<CompleteMultipartUploadResult/>")

    def do_HEAD(self):
        key, _ = self._key()
        if key not in self.objects:
            return self.reply(404)
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.objects[key])))
        self.end_headers()

    def do_GET(self):
        key, _ = self._key()
        data = self.objects.get(key)
        if data is None:
            return self.reply(404, b"<Error><Message>NoSuchKey</Message></Error>")
        m = re.match(r"bytes=(\d+)-(\d+)", self.headers.get("Range") or "")
        if m:
            a, b = int(m.group(1)), min(int(m.group(2)), len(data) - 1)
            return self.reply(206, data[a:b + 1])
        self.reply(200, data)

    def do_DELETE(self):
        key, q = self._key()
        if "uploadId" in q:
            self.uploads.pop(q["uploadId"], None)
        else:
            self.objects.pop(key, None)
        self.reply(204)


class FakeArr(BaseHTTPRequestHandler):
    """Radarr / Sonarr : juste ce dont Nexis se sert."""
    calls = []
    movies = []
    series = []
    episodes = []
    files = {}
    protocol_version = "HTTP/1.1"
    reply = FakeDAV.reply
    _body = FakeDAV._body

    def log_message(self, *a):
        pass

    def _ok(self):
        if self.headers.get("X-Api-Key") != "abcdef0123456789abcdef0123456789":
            self.reply(401)
            return False
        return True

    def _json(self, obj, status=200):
        self.reply(status, json.dumps(obj).encode(), {"Content-Type": "application/json"})

    def do_GET(self):
        if not self._ok():
            return
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        self.calls.append(("GET", u.path))
        if u.path == "/api/v3/system/status":
            return self._json({"appName": "Radarr", "version": "5.0"})
        if u.path == "/api/v3/movie":
            return self._json([m for m in self.movies if str(m["tmdbId"]) == q.get("tmdbId")])
        if u.path == "/api/v3/series":
            return self._json([x for x in self.series if "tvdbId" not in q or str(x["tvdbId"]) == q["tvdbId"]])
        if u.path == "/api/v3/episode":
            return self._json(self.episodes)
        m = re.match(r"^/api/v3/episodefile/(\d+)$", u.path)
        if m:
            return self._json(self.files[int(m.group(1))])
        self.reply(404)

    def do_PUT(self):
        if self._ok():
            self.calls.append(("PUT", self.path, json.loads(self._body() or b"{}")))
            self._json({}, 202)

    def do_DELETE(self):
        if self._ok():
            self.calls.append(("DELETE", self.path))
            self._json({})


# faux rclone : « remote:chemin » -> dossier $FAKE_RCLONE_ROOT/remote/chemin
FAKE_RCLONE = r'''#!/usr/bin/env python3
import json, os, sys
args = [a for a in sys.argv[1:]]
if "--config" in args:
    i = args.index("--config"); del args[i:i + 2]
root = os.environ["FAKE_RCLONE_ROOT"]
def path(t):
    remote, _, p = t.partition(":")
    return os.path.join(root, remote, p.lstrip("/"))
cmd = args[0]
if cmd == "rcat":
    p = path(args[-1]); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(sys.stdin.buffer.read())
elif cmd == "cat":
    off = int(args[args.index("--offset") + 1]); cnt = int(args[args.index("--count") + 1])
    with open(path(args[-1]), "rb") as f:
        f.seek(off); sys.stdout.buffer.write(f.read(cnt))
elif cmd == "lsjson":
    p = path(args[-1])
    if not os.path.exists(p):
        sys.stderr.write("ERROR : object not found\n"); sys.exit(3)
    print(json.dumps({"Size": os.path.getsize(p)}))
elif cmd == "deletefile":
    p = path(args[-1])
    if not os.path.exists(p):
        sys.stderr.write("ERROR : object not found\n"); sys.exit(4)
    os.remove(p)
'''


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# SFTP : vrai serveur (asyncssh), seulement si la bibliothèque est disponible (dossier donné par NEXIS_TEST_PYDEPS)
SFTPD = r'''
import asyncio, os, sys
sys.path.insert(0, sys.argv[1])
import asyncssh
root, key, port_file = sys.argv[2], sys.argv[3], sys.argv[4]
asyncssh.generate_private_key("ssh-ed25519").write_private_key(key)
class S(asyncssh.SSHServer):
    def begin_auth(self, u): return True
    def password_auth_supported(self): return True
    def validate_password(self, u, p): return u == "nexis" and p == "secret"
async def main():
    srv = await asyncssh.create_server(S, "127.0.0.1", 0, server_host_keys=[key],
                                       sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=root))
    open(port_file, "w").write(str(srv.sockets[0].getsockname()[1]))
    await asyncio.Future()
asyncio.run(main())
'''


def pydeps():
    d = os.environ.get("NEXIS_TEST_PYDEPS")
    return d if d and os.path.isdir(os.path.join(d, "asyncssh")) else None


class ColdStorageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        FakeDAV.files, FakeDAV.dirs = {}, {"/dav"}
        FakeS3.objects, FakeS3.uploads = {}, {}
        cls.dav = serve(FakeDAV)
        cls.s3 = serve(FakeS3)
        # bibliothèque propre au test (on y efface un fichier)
        src = os.path.join(media_root(), "films")
        cls.films = os.path.join(cls.work, "films")
        os.makedirs(cls.films)
        for f in ("Nuit.Polaire.2021.1080p.BluRay.x264.mkv", "Nuit.Polaire.2021.1080p.BluRay.x264.en.srt"):
            shutil.copy2(os.path.join(src, f), os.path.join(cls.films, f))
        shutil.copytree(os.path.join(src, "Brume (2022)"), os.path.join(cls.films, "Brume (2022)"), copy_function=shutil.copy2)
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.server = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        cls.port = cls.server.server_address[1]
        cls.ctx.http_port = cls.port
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        st, r = cls.admin.call("POST", "/api/admin/libraries", {"name": "Films", "kind": "movie", "paths": [cls.films]})
        assert st == 200, r
        wait_scan(cls.admin)
        cls.movie = cls.ctx.db.one("SELECT * FROM items WHERE type='movie' AND key LIKE '%Nuit.Polaire%'")
        # faux rclone sur le PATH
        cls.bin = os.path.join(cls.work, "bin")
        os.makedirs(cls.bin)
        exe = os.path.join(cls.bin, "rclone")
        with open(exe, "w") as f:
            f.write(FAKE_RCLONE.replace("/usr/bin/env python3", sys.executable))
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        os.environ["FAKE_RCLONE_ROOT"] = os.path.join(cls.work, "rclone")
        cls.old_path = os.environ["PATH"]
        os.environ["PATH"] = cls.bin + os.pathsep + os.environ["PATH"]

    @classmethod
    def tearDownClass(cls):
        os.environ["PATH"] = cls.old_path
        cls.ctx.dlna.stop()
        cls.server.shutdown()
        cls.dav.shutdown()
        cls.s3.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def dav_conf(self):
        return {"url": f"http://127.0.0.1:{self.dav.server_address[1]}/dav", "username": "nexis", "password": "secret"}

    def s3_conf(self):
        return {"endpoint": f"http://127.0.0.1:{self.s3.server_address[1]}", "region": "eu-west-3", "bucket": "nexis",
                "prefix": "archive", "access_key": "AKIAEXAMPLE", "secret_key": "secretsecret"}

    def wait_jobs(self, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            st, r = self.admin.call("GET", "/api/admin/cold")
            if not [j for j in r["jobs"] if j["state"] in ("queued", "running")]:
                return r
            time.sleep(0.2)
        raise AssertionError("archivage trop long")

    # -------------------------------------------------------------------------- stockages
    def exercise(self, b):
        data = os.urandom(3 * 1024 * 1024 + 123)
        b.check()
        b.put("Films/Un film (2020)/film é.mkv", cold_mod._BytesReader(data), len(data))
        self.assertEqual(b.size("Films/Un film (2020)/film é.mkv"), len(data))
        for a, z in ((0, 99), (1_000_000, 2_500_000), (len(data) - 50, len(data) - 1)):
            self.assertEqual(b"".join(b.open("Films/Un film (2020)/film é.mkv", a, z)), data[a:z + 1], (a, z))
        b.delete("Films/Un film (2020)/film é.mkv")
        self.assertIsNone(b.size("Films/Un film (2020)/film é.mkv"))
        b.delete("Films/Un film (2020)/film é.mkv")  # déjà absent : pas d'erreur

    def test_01_backends(self):
        cold = self.ctx.cold
        os.makedirs(os.path.join(self.work, "usb"), exist_ok=True)
        self.exercise(cold_mod.Local({"path": os.path.join(self.work, "usb")}, cold))
        self.assertFalse(os.listdir(os.path.join(self.work, "usb")))  # dossiers vides retirés
        self.exercise(cold_mod.WebDAV(self.dav_conf(), cold))
        old = cold_mod.PART
        cold_mod.PART = 1024 * 1024  # envoi en plusieurs parties dès 1 Mo
        try:
            self.exercise(cold_mod.S3(self.s3_conf(), cold))
        finally:
            cold_mod.PART = old
        self.assertEqual(FakeS3.uploads, {})  # parties assemblées
        self.exercise(cold_mod.Rclone({"remote": "pcloud:Nexis"}, cold))
        # mauvais mot de passe WebDAV : message clair
        with self.assertRaises(cold_mod.ColdError) as e:
            cold_mod.WebDAV({**self.dav_conf(), "password": "faux"}, cold).check()
        self.assertIn("refusé", str(e.exception))

    def test_02_sftp(self):
        deps = pydeps()
        if not deps or not cold_mod.curl_sftp():
            self.skipTest("serveur SFTP de test indisponible (NEXIS_TEST_PYDEPS avec asyncssh) ou curl sans SFTP")
        root = os.path.join(self.work, "sftp")
        os.makedirs(root)
        port_file = os.path.join(self.work, "sftp.port")
        p = subprocess.Popen([sys.executable, "-c", SFTPD, deps, root, os.path.join(self.work, "hostkey"), port_file])
        try:
            for _ in range(100):
                if os.path.exists(port_file) and open(port_file).read():
                    break
                time.sleep(0.1)
            port = int(open(port_file).read())
            conf = {"host": "127.0.0.1", "port": port, "username": "nexis", "password": "secret", "path": "nexis"}
            b = cold_mod.SFTP(conf, self.ctx.cold)
            fp = b.fingerprint()
            self.assertRegex(fp, r"^[0-9a-f]{32}$")
            with self.assertRaises(cold_mod.ColdError):  # clé non confirmée : refus
                cold_mod.SFTP({**conf, "hostkey": "0" * 32}, self.ctx.cold).check()
            self.exercise(cold_mod.SFTP({**conf, "hostkey": fp}, self.ctx.cold))
            # par l'API : l'essai donne l'empreinte à confirmer, puis le stockage s'ajoute
            st, r = self.admin.call("POST", "/api/admin/cold/test", {"kind": "sftp", "conf": conf})
            self.assertEqual((st, r.get("fingerprint")), (200, fp), r)
            st, r = self.admin.call("POST", "/api/admin/cold/stores", {"kind": "sftp", "name": "Box", "conf": {**conf, "hostkey": fp}})
            self.assertEqual((st, r["ok"]), (200, True), r)
            st, r = self.admin.call("GET", "/api/admin/cold")
            box = [s for s in r["stores"] if s["kind"] == "sftp"][0]
            self.assertNotIn("password", box["conf"])
            self.assertEqual(box["conf"]["secrets_set"], ["password"])
            self.admin.call("DELETE", f"/api/admin/cold/stores/{box['id']}")
        finally:
            p.kill()
            p.wait()

    def test_03_store_admin(self):
        st, r = self.admin.call("POST", "/api/admin/cold/stores", {"kind": "local", "name": "Système", "conf": {"path": "/etc"}})
        self.assertEqual(st, 400)
        st, r = self.admin.call("POST", "/api/admin/cold/stores", {"kind": "webdav", "name": "Mauvais",
                                                                    "conf": {**self.dav_conf(), "password": "faux"}})
        self.assertEqual(st, 400, r)  # essai réel avant l'ajout
        st, r = self.admin.call("GET", "/api/admin/cold")
        self.assertEqual(r["stores"], [])
        self.assertIsNone(r["available"]["webdav"])
        self.assertIsNotNone(r["available"]["smb"])  # pas de service de montage dans les tests

    # -------------------------------------------------------------------------- archivage et lecture
    def test_04_archive_and_play_from_cold(self):
        usb = os.path.join(self.work, "disque-usb")
        os.makedirs(usb)
        st, r = self.admin.call("POST", "/api/admin/cold/stores", {"kind": "local", "name": "Disque USB", "conf": {"path": usb}})
        self.assertEqual(st, 200, r)
        local_id = r["id"]
        st, r = self.admin.call("POST", "/api/admin/cold/stores", {"kind": "webdav", "name": "Nextcloud", "conf": self.dav_conf()})
        self.assertEqual(st, 200, r)
        dav_id = r["id"]
        mid = self.movie["id"]
        for sid in (local_id, dav_id):
            st, r = self.admin.call("POST", "/api/admin/cold/archive", {"ids": [mid], "store": sid})
            self.assertEqual((st, r["queued"]), (200, 1), r)
        r = self.wait_jobs()
        self.assertEqual(r["jobs"], [], r["jobs"])
        counts = {s["id"]: s for s in r["stores"]}
        self.assertEqual(counts[local_id]["copies"], 1)
        self.assertEqual(counts[dav_id]["bytes"], os.path.getsize(self.movie["key"]))
        # copie lisible telle quelle sur le disque : même arborescence que la bibliothèque, sous-titres voisins compris
        self.assertTrue(os.path.isfile(os.path.join(usb, "Films", "Nuit.Polaire.2021.1080p.BluRay.x264.mkv")))
        self.assertTrue(os.path.isfile(os.path.join(usb, "Films", "Nuit.Polaire.2021.1080p.BluRay.x264.en.srt")))
        st, c = self.admin.call("GET", f"/api/admin/cold/items/{mid}")
        self.assertEqual(sorted(x["store"] for x in c["copies"]), ["Disque USB", "Nextcloud"])
        self.assertTrue(all(x["verified_at"] for x in c["copies"]))
        st, d = self.admin.call("GET", f"/api/items/{mid}")
        self.assertEqual((d["archived"], d["cold_only"]), (True, False))
        # déjà archivé : rien de plus dans la file
        st, r = self.admin.call("POST", "/api/admin/cold/archive", {"ids": [mid], "store": dav_id})
        self.assertEqual(r["queued"], 0)

        # le fichier quitte la bibliothèque (supprimé par Radarr, par exemple) : le titre reste, lu depuis la copie
        os.remove(self.movie["key"])
        os.remove(self.movie["key"].replace(".mkv", ".en.srt"))
        self.admin.call("POST", "/api/admin/scan")
        wait_scan(self.admin)
        st, d = self.admin.call("GET", f"/api/items/{mid}")
        self.assertEqual(st, 200)
        self.assertEqual((d["archived"], d["cold_only"]), (True, True))
        self.assertEqual(d["title"], self.movie["title"])
        st, p = self.admin.call("POST", "/api/play", {"id": str(mid)})
        self.assertEqual(st, 200, p)
        self.assertEqual(self.ctx.transcoder.get(p["stream"]).path, os.path.join(usb, "Films", "Nuit.Polaire.2021.1080p.BluRay.x264.mkv"))
        n, data, _ = hls_segment(self.admin, p["url"], at=10.0)
        self.assertEqual(ffprobe_codec(data), "h264")
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})

        # plus de disque USB : la copie WebDAV prend le relais, relue par l'adresse locale de Nexis
        st, r = self.admin.call("DELETE", f"/api/admin/cold/items/{mid}/{local_id}")
        self.assertEqual((st, r["ok"]), (200, True), r)
        self.assertFalse(os.path.exists(os.path.join(usb, "Films")))
        st, p = self.admin.call("POST", "/api/play", {"id": str(mid)})
        self.assertEqual(st, 200, p)
        self.assertEqual(p["mode"], "copy")
        self.assertTrue(self.ctx.transcoder.get(p["stream"]).path.startswith("cold:http://127.0.0.1:"))
        n, data, _ = hls_segment(self.admin, p["url"], at=10.0)
        self.assertEqual(ffprobe_codec(data), "h264")
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})
        # l'adresse locale ne sert qu'à ffmpeg : jeton secret, et jamais derrière un proxy
        url = self.ctx.cold.stream_url(dav_id, "Films/Nuit.Polaire.2021.1080p.BluRay.x264.mkv", 100)
        path = urllib.parse.urlsplit(url).path
        st, body, h = Client(self.base).call("GET", path, headers={"Range": "bytes=0-3"}, raw=True)
        self.assertEqual((st, body), (206, b"\x1aE\xdf\xa3"))  # début d'un MKV
        self.assertEqual(Client(self.base).call("GET", path, headers={"X-Forwarded-For": "1.2.3.4"}, raw=True)[0], 404)
        self.assertEqual(Client(self.base).call("GET", "/cold/" + "x" * 32, raw=True)[0], 404)
        # téléchargement hors ligne depuis la copie froide
        st, j = self.admin.call("POST", "/api/downloads", {"id": str(mid), "quality": "original",
                                                            "caps": {"video": ["h264"], "audio": ["aac"], "containers": ["mp4"]}})
        self.assertEqual(st, 200, j)
        for _ in range(200):
            st, j2 = self.admin.call("GET", f"/api/downloads/{j['id']}")
            if j2["state"] in ("ready", "error"):
                break
            time.sleep(0.2)
        self.assertEqual(j2["state"], "ready", j2)
        # dernière copie : on ne la retire pas, ni le stockage qui la porte
        st, r = self.admin.call("DELETE", f"/api/admin/cold/items/{mid}/{dav_id}")
        self.assertEqual(st, 409, r)
        st, r = self.admin.call("DELETE", f"/api/admin/cold/stores/{dav_id}")
        self.assertEqual(st, 409, r)

    def test_05_show_and_errors(self):
        # un stockage injoignable : la tâche passe en erreur, avec un message, et se relance
        st, r = self.admin.call("POST", "/api/admin/cold/stores", {"kind": "s3", "name": "B2", "conf": self.s3_conf()})
        self.assertEqual(st, 200, r)
        s3_id = r["id"]
        brume = self.ctx.db.one("SELECT id FROM items WHERE type='movie' AND title LIKE 'Brume%'")
        self.s3.shutdown()
        self.s3.server_close()
        try:
            self.admin.call("POST", "/api/admin/cold/archive", {"ids": [brume["id"]], "store": s3_id})
            deadline = time.time() + 30
            while time.time() < deadline:
                st, r = self.admin.call("GET", "/api/admin/cold")
                errs = [j for j in r["jobs"] if j["state"] == "error"]
                if errs:
                    break
                time.sleep(0.2)
            self.assertTrue(errs and errs[0]["error"], r["jobs"])
            self.assertIn("Brume", errs[0]["title"])
        finally:
            type(self).s3 = serve(FakeS3)
        conf = {**self.s3_conf(), "endpoint": f"http://127.0.0.1:{self.s3.server_address[1]}"}
        st, r = self.admin.call("PUT", f"/api/admin/cold/stores/{s3_id}", {"conf": conf})
        self.assertEqual(st, 200, r)
        st, r = self.admin.call("POST", f"/api/admin/cold/jobs/{errs[0]['id']}/retry")
        self.wait_jobs()
        st, c = self.admin.call("GET", f"/api/admin/cold/items/{brume['id']}")
        self.assertEqual([x["store"] for x in c["copies"]], ["B2"])
        self.assertIn("/nexis/archive/Films/Brume (2022)/Brume.2022.HEVC.mkv", FakeS3.objects)  # seau/préfixe/arborescence

    # -------------------------------------------------------------------------- titres ramenés en local
    def test_05b_cache_brings_titles_back(self):
        mid = self.movie["id"]
        if self.ctx.cold.local_missing(self.movie) is False:
            self.skipTest("dépend de test_04 (fichier retiré de la bibliothèque)")
        self.ctx.db.set_setting("cold_cache_gb", "1")
        self.ctx.db.set_setting("cold_cache_free_gb", "0")
        # la lecture en ligne du test 04 l'a peut-être déjà ramené : on repart d'un cache vide
        for _ in range(100):
            if not (self.ctx.cold.fetcher and self.ctx.cold.fetcher.is_alive()):
                break
            time.sleep(0.1)
        self.admin.call("PUT", "/api/admin/cold/extra", {"clear_cache": True})
        st, p = self.admin.call("POST", "/api/play", {"id": str(mid)})
        self.assertEqual(st, 200, p)
        self.assertTrue(self.ctx.transcoder.get(p["stream"]).path.startswith("cold:"))  # en ligne, cette fois
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})
        for _ in range(100):
            if self.ctx.db.scalar("SELECT 1 FROM cold_cache WHERE item_id=?", (mid,)):
                break
            time.sleep(0.1)
        path = self.ctx.db.scalar("SELECT path FROM cold_cache WHERE item_id=?", (mid,))
        self.assertTrue(path and os.path.getsize(path) == self.movie["file_size"], path)
        st, p = self.admin.call("POST", "/api/play", {"id": str(mid)})
        self.assertEqual(self.ctx.transcoder.get(p["stream"]).path, path)  # la suite : depuis le disque local
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})
        st, r = self.admin.call("GET", "/api/admin/cold/extra")
        self.assertEqual((r["cache"]["count"], r["cache"]["bytes"]), (1, self.movie["file_size"]))
        # budget trop petit : rien n'est ramené ; vider le cache le retire
        self.admin.call("PUT", "/api/admin/cold/extra", {"clear_cache": True, "cache_gb": 0.001})
        self.assertFalse(os.path.exists(path))
        self.assertFalse(self.ctx.cold._make_room(self.movie["file_size"]))
        self.admin.call("PUT", "/api/admin/cold/extra", {"cache_gb": 20})

    # -------------------------------------------------------------------------- libérer de la place
    def free_state(self, root):
        st, r = self.admin.call("GET", "/api/admin/cold/free")
        return [g for g in r["groups"] if g["root"] == root]

    def test_07_free_space_rule(self):
        db = self.ctx.db
        brume = db.one("SELECT * FROM items WHERE type='movie' AND title LIKE 'Brume%'")
        lib = brume["library_id"]
        old = int(time.time()) - 60 * 86400
        db.run("UPDATE items SET added_at=?", (old,))
        db.run("DELETE FROM progress")
        db.run("DELETE FROM play_log")
        db.run("DELETE FROM history")
        db.run("DELETE FROM streams")
        usb = os.path.join(self.work, "usb-free")
        os.makedirs(usb)
        st, r = self.admin.call("POST", "/api/admin/cold/stores", {"kind": "local", "name": "USB 2", "conf": {"path": usb}})
        store = r["id"]
        st, r = self.admin.call("PUT", "/api/admin/cold/free", {"enabled": True, "days": 30, "mode": "review", "store": store,
                                                                 "delay_days": 7, "methods": {str(lib): "write"}})
        self.assertEqual(st, 200, r)
        st, prev = self.admin.call("GET", "/api/admin/cold/free/preview")
        self.assertEqual([x["title"] for x in prev["items"]], [brume["title"]])  # Nuit polaire : déjà hors de la bibliothèque
        # titre « gardé sur le disque » : jamais proposé
        self.admin.call("POST", "/api/admin/cold/free/keep", {"root": brume["id"]})
        st, prev = self.admin.call("GET", "/api/admin/cold/free/preview")
        self.assertEqual(prev["count"], 0)
        self.admin.call("POST", "/api/admin/cold/free/keep", {"root": brume["id"], "keep": False})
        # à valider : proposé seulement
        st, r = self.admin.call("POST", "/api/admin/cold/free/run")
        self.assertEqual(r["new"], 1, r)
        self.assertEqual(self.free_state(brume["id"])[0]["state"], "proposed")
        self.assertTrue(os.path.isfile(brume["key"]))
        # validé : copie (déjà faite sur S3 en test 05 : elle suffit), puis 7 jours de délai
        st, r = self.admin.call("POST", "/api/admin/cold/free/approve", {"roots": [brume["id"]]})
        self.assertEqual(r["approved"], 1)
        g = self.free_state(brume["id"])[0]
        self.assertEqual(g["state"], "pending", g)
        self.assertGreater(g["delete_after"], time.time() + 6 * 86400)
        # regardé pendant le délai : on garde
        db.run("INSERT INTO progress(user_id, rating_key, position_ms, duration_ms, updated_at) VALUES(1, ?, 1000, 5000, ?)",
               (str(brume["id"]), int(time.time())))
        self.admin.call("POST", "/api/admin/cold/free/run")
        self.assertEqual(self.free_state(brume["id"]), [])
        self.assertTrue(os.path.isfile(brume["key"]))
        # plus tard, toujours pas regardé, en automatique et sans délai : supprimé de la bibliothèque, lu depuis la copie
        db.run("DELETE FROM progress")
        self.admin.call("PUT", "/api/admin/cold/free", {"mode": "auto", "delay_days": 0})
        st, r = self.admin.call("POST", "/api/admin/cold/free/run")
        st, r = self.admin.call("POST", "/api/admin/cold/free/run")  # copie vérifiée -> suppression
        self.assertFalse(os.path.exists(brume["key"]), r)
        st, r = self.admin.call("GET", "/api/admin/cold/free")
        self.assertEqual(r["freed"]["n"], 1)
        self.assertEqual(r["freed"]["bytes"], brume["file_size"])
        st, d = self.admin.call("GET", f"/api/items/{brume['id']}")
        self.assertEqual((st, d["cold_only"]), (200, True))
        self.admin.call("PUT", "/api/admin/cold/free", {"enabled": False})

    def test_08_radarr_sonarr(self):
        srv = serve(FakeArr)
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}"
            key = "abcdef0123456789abcdef0123456789"
            st, r = self.admin.call("POST", "/api/admin/cold/free/check", {"which": "radarr", "url": url, "key": key})
            self.assertEqual((st, r.get("version")), (200, "5.0"), r)
            st, r = self.admin.call("POST", "/api/admin/cold/free/check", {"which": "radarr", "url": url, "key": "x" * 32})
            self.assertEqual(st, 400)
            arr = coldfree.Arr(url, key, "Radarr")
            row = {"tmdb_id": 42, "key": "/media/films/Film (2020)/Film.2020.mkv", "file_size": 1234}
            FakeArr.movies = [{"id": 7, "tmdbId": 42, "movieFile": {"id": 70, "path": "/data/films/Film (2020)/Film.2020.mkv", "size": 1234}}]
            FakeArr.calls = []
            coldfree.radarr_delete(arr, row)
            self.assertEqual([c[0] for c in FakeArr.calls], ["GET", "PUT", "DELETE"])
            self.assertEqual(FakeArr.calls[1][2], {"movieIds": [7], "monitored": False})  # plus surveillé, d'abord
            self.assertEqual(FakeArr.calls[2][1], "/api/v3/moviefile/70")
            # pas le même fichier (autre version) : rien n'est supprimé
            FakeArr.movies[0]["movieFile"]["size"] = 999
            FakeArr.calls = []
            with self.assertRaises(coldfree.FreeError):
                coldfree.radarr_delete(arr, row)
            self.assertFalse([c for c in FakeArr.calls if c[0] == "DELETE"])
            # Sonarr : épisode retrouvé par saison / numéro, fichier à deux épisodes : les deux ne sont plus surveillés
            FakeArr.series = [{"id": 3, "tvdbId": 555, "tmdbId": 99}]
            FakeArr.episodes = [{"id": 31, "seasonNumber": 1, "episodeNumber": 1, "episodeFileId": 300},
                                {"id": 32, "seasonNumber": 1, "episodeNumber": 2, "episodeFileId": 300},
                                {"id": 33, "seasonNumber": 1, "episodeNumber": 3, "episodeFileId": 301}]
            FakeArr.files = {300: {"id": 300, "path": "/tv/Show/S01/Show.S01E01E02.mkv", "size": 50}}
            FakeArr.calls = []
            coldfree.sonarr_delete(coldfree.Arr(url, key, "Sonarr"), {"idx": 1, "key": "/m/Show/S01/Show.S01E01E02.mkv", "file_size": 50},
                                   {"tvdb_id": 555}, 1)
            self.assertIn(("PUT", "/api/v3/episode/monitor", {"episodeIds": [31, 32], "monitored": False}), FakeArr.calls)
            self.assertEqual(FakeArr.calls[-1], ("DELETE", "/api/v3/episodefile/300"))
            # par l'API : méthode par défaut « Radarr / Sonarr » dès qu'ils sont configurés
            st, r = self.admin.call("PUT", "/api/admin/cold/free", {"radarr_url": url, "radarr_key": key})
            self.assertEqual(r["conf"]["secrets_set"], ["radarr_key"])
            self.assertNotIn("radarr_key", r["conf"])
            st, r = self.admin.call("GET", "/api/admin/cold/free")
            self.assertEqual({l["kind"]: l["method"] for l in r["libraries"]}["movie"], "write")  # choisi en test 07
            self.assertEqual(self.admin.call("PUT", "/api/admin/cold/free", {"radarr_key": "court"})[0], 400)
        finally:
            srv.shutdown()

    def test_09_compress_archives(self):
        """Archives réencodées en HEVC (processeur ici) : plus petites, lues avec leurs propres pistes."""
        mid = self.movie["id"]
        st, c = self.admin.call("GET", "/api/admin/cold/compress")
        self.assertIsNone(c["hw"])  # pas de carte graphique : l'interface le signale
        self.assertGreaterEqual(c["todo"]["count"], 1)  # Nuit polaire (H.264) sur WebDAV
        before = self.ctx.db.one("SELECT * FROM cold_copies WHERE item_id=? AND compressed=0", (mid,))
        old_gain = cold_mod.MIN_GAIN
        cold_mod.MIN_GAIN = 5  # vidéo de test minuscule : on accepte tout gain
        try:
            self.admin.call("PUT", "/api/admin/cold/settings", {"compress": True, "compress_quality": "small"})
            st, r = self.admin.call("POST", "/api/admin/cold/compress", {})
            self.assertGreaterEqual(r["queued"], 1, r)
            self.wait_jobs(300)
        finally:
            cold_mod.MIN_GAIN = old_gain
        after = self.ctx.db.one("SELECT * FROM cold_copies WHERE item_id=? AND store_id=?", (mid, before["store_id"]))
        self.assertEqual(after["compressed"], 1, after)
        self.assertTrue(after["name"].endswith(".mkv"))
        self.assertEqual(after["orig_size"], before["size"])
        self.assertEqual(json.loads(after["probe"])["video"]["codec"], "hevc")
        self.assertEqual(len(json.loads(after["probe"])["audio"]), 2)  # les deux pistes son gardées
        if after["name"] != before["name"]:
            self.assertNotIn(urllib.parse.unquote("/dav/" + before["name"]), FakeDAV.files)  # ancienne copie effacée
        # lecture : la copie compressée décide (HEVC : conversion pour un navigateur qui ne le lit pas)
        self.admin.call("PUT", "/api/admin/cold/extra", {"clear_cache": True})
        st, p = self.admin.call("POST", "/api/play", {"id": str(mid), "caps": {"video": ["h264"], "audio": ["aac"], "containers": ["mp4"]}})
        self.assertEqual(st, 200, p)
        self.assertEqual(p["mode"], "transcode", p)
        n, data, _ = hls_segment(self.admin, p["url"], at=5.0)
        self.assertEqual(ffprobe_codec(data), "h264")
        self.admin.call("POST", "/api/stop", {"stream": p["stream"]})
        st, c = self.admin.call("GET", "/api/admin/cold/compress")
        self.assertGreaterEqual(c["done"]["n"], 1)
        # vidéo déjà en HEVC : archivée sans changement
        brume = self.ctx.db.one("SELECT id FROM items WHERE title LIKE 'Brume%'")
        usb = os.path.join(self.work, "usb-hevc")
        os.makedirs(usb)
        st, r = self.admin.call("POST", "/api/admin/cold/stores", {"kind": "local", "name": "USB 3", "conf": {"path": usb}})
        self.admin.call("POST", "/api/admin/cold/archive", {"ids": [brume["id"]], "store": r["id"]})
        self.wait_jobs()
        cp = self.ctx.db.one("SELECT compressed FROM cold_copies WHERE item_id=? AND store_id=?", (brume["id"], r["id"]))
        self.assertEqual(cp["compressed"], 0)
        self.admin.call("PUT", "/api/admin/cold/settings", {"compress": False})

    def test_06_mountd_writable_only_for_cold(self):
        m = mountd.Mounts.__new__(mountd.Mounts)
        args = {"host": "nas.local", "share": "media", "username": "u", "password": "p", "version": "auto", "writable": True}
        with self.assertRaises(mountd.MountError):
            m.validate({**args, "name": "films"})
        self.assertTrue(m.validate({**args, "name": "cold-3"})["writable"])
        self.assertFalse(m.validate({**args, "name": "films", "writable": False})["writable"])


if __name__ == "__main__":
    unittest.main()
