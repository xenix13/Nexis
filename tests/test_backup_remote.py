"""Sauvegardes : chiffrement, noms par serveur, stockages externes (faux S3, Google Drive, OneDrive),
restauration en une étape depuis un fichier ou un chemin.

python3 -m unittest tests.test_backup_remote
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.test_api import STATIC, Client

from seance import backup, remote_storage
from seance.api import Ctx, build_app
from seance.web import Server, make_handler


class FakeCloud(BaseHTTPRequestHandler):
    """S3 (/s3/<bucket>/…), Google (/oauth/…, /drive/…, /upload/…) et Microsoft (/ms/…, /graph/…) en un seul faux."""
    objects = {}       # nom -> octets (tous services confondus, préfixés)
    polls = {"g": 0, "m": 0}
    sessions = {}
    auth_seen = []

    def log_message(self, *a):
        pass

    def reply(self, status, obj=None, ctype="application/json", headers=None):
        body = obj if isinstance(obj, bytes) else (json.dumps(obj).encode() if obj is not None else b"")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def form(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.body().decode()).items()}

    # ---------------- S3
    def s3(self, method):
        auth = self.headers.get("Authorization") or ""
        FakeCloud.auth_seen.append(auth)
        if "Credential=AKIATEST/" not in auth or "Signature=" not in auth:
            return self.reply(403, b"<Error><Message>SignatureDoesNotMatch</Message></Error>", "application/xml")
        u = urllib.parse.urlparse(self.path)
        parts = u.path.split("/", 3)  # '', 's3', bucket, key
        if parts[2] != "sauvegardes":
            return self.reply(404, b"<Error><Message>NoSuchBucket</Message></Error>", "application/xml")
        key = urllib.parse.unquote(parts[3]) if len(parts) > 3 else ""
        if method == "PUT":
            FakeCloud.objects["s3:" + key] = self.body()
            return self.reply(200, b"")
        if method == "DELETE":
            FakeCloud.objects.pop("s3:" + key, None)
            return self.reply(204, b"")
        if key:
            data = FakeCloud.objects.get("s3:" + key)
            return self.reply(200, data, "application/octet-stream") if data is not None else self.reply(404, b"")
        prefix = urllib.parse.parse_qs(u.query).get("prefix", [""])[0]
        items = "".join(f"<Contents><Key>{k[3:]}</Key><Size>{len(v)}</Size><LastModified>2026-11-07T00:00:00Z</LastModified></Contents>"
                        for k, v in FakeCloud.objects.items() if k.startswith("s3:" + prefix))
        xml = f'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{items}<IsTruncated>false</IsTruncated></ListBucketResult>'
        return self.reply(200, xml.encode(), "application/xml")

    def do_PUT(self):
        if self.path.startswith("/s3/"):
            return self.s3("PUT")
        if self.path.startswith("/upload-session/g/"):
            sid = self.path.rsplit("/", 1)[1]
            FakeCloud.objects["g:" + FakeCloud.sessions[sid]] = self.body()
            return self.reply(200, {"id": "g-" + sid})
        if self.path.startswith("/upload-session/m/"):
            sid = self.path.rsplit("/", 1)[1]
            rng = self.headers.get("Content-Range")
            start = int(re.match(r"bytes (\d+)-", rng).group(1))
            name = FakeCloud.sessions[sid]
            buf = FakeCloud.objects.get("m:" + name, b"")[:start] + self.body()
            FakeCloud.objects["m:" + name] = buf
            total = int(rng.rsplit("/", 1)[1])
            return self.reply(201 if len(buf) >= total else 202, {"id": "m-" + name} if len(buf) >= total else {})
        self.reply(404)

    def do_DELETE(self):
        if self.path.startswith("/s3/"):
            return self.s3("DELETE")
        m = re.match(r"^/drive/v3/files/g-(.+)$", self.path) or re.match(r"^/graph/me/drive/items/m-(.+)$", self.path)
        if m:
            prefix = "g:" if self.path.startswith("/drive") else "m:"
            key = FakeCloud.sessions.get(m.group(1), urllib.parse.unquote(m.group(1)))
            FakeCloud.objects.pop(prefix + key, None)
            return self.reply(204)
        self.reply(404)

    def do_GET(self):
        if self.path.startswith("/s3/"):
            return self.s3("GET")
        tok = self.headers.get("Authorization")
        if self.path.startswith("/drive/v3/files"):
            if tok != "Bearer G-ACCESS":
                return self.reply(401, {"error": {"message": "auth"}})
            u = urllib.parse.urlparse(self.path)
            m = re.match(r"^/drive/v3/files/g-(.+)$", u.path)
            if m:
                return self.reply(200, FakeCloud.objects["g:" + FakeCloud.sessions[m.group(1)]], "application/octet-stream")
            q = urllib.parse.parse_qs(u.query).get("q", [""])[0]
            if "mimeType='application/vnd.google-apps.folder'" in q:
                return self.reply(200, {"files": []})
            sid_of = {v: k for k, v in FakeCloud.sessions.items()}
            return self.reply(200, {"files": [{"id": "g-" + sid_of[k[2:]], "name": k[2:], "size": str(len(v))}
                                              for k, v in FakeCloud.objects.items() if k.startswith("g:")]})
        if self.path.startswith("/graph/me/drive/special/approot/children"):
            if tok != "Bearer M-ACCESS":
                return self.reply(401, {"error": {"message": "auth"}})
            return self.reply(200, {"value": [{"id": "m-" + urllib.parse.quote(k[2:]), "name": k[2:], "size": len(v), "file": {}}
                                              for k, v in FakeCloud.objects.items() if k.startswith("m:")]})
        m = re.match(r"^/graph/me/drive/items/m-(.+)/content$", self.path)
        if m:
            return self.reply(200, FakeCloud.objects["m:" + urllib.parse.unquote(m.group(1))], "application/octet-stream")
        self.reply(404)

    def do_POST(self):
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        if self.path == "/oauth/device/code":
            f = self.form()
            assert f["scope"] == remote_storage.GOOGLE_SCOPE
            return self.reply(200, {"device_code": "GDEV", "user_code": "GOOG-123", "verification_url": "https://www.google.com/device",
                                    "expires_in": 900, "interval": 1})
        if self.path == "/oauth/token":
            f = self.form()
            if f.get("grant_type") == "refresh_token":
                return self.reply(200, {"access_token": "G-ACCESS", "expires_in": 3600})
            FakeCloud.polls["g"] += 1
            if FakeCloud.polls["g"] == 1:
                return self.reply(428, {"error": "authorization_pending"})
            return self.reply(200, {"access_token": "G-ACCESS", "refresh_token": "G-REFRESH", "expires_in": 3600})
        if self.path == "/ms/devicecode":
            return self.reply(200, {"device_code": "MDEV", "user_code": "MS-456", "verification_uri": "https://microsoft.com/devicelogin",
                                    "expires_in": 900, "interval": 1})
        if self.path == "/ms/token":
            FakeCloud.polls["m"] += 1
            if FakeCloud.polls["m"] == 1:
                return self.reply(400, {"error": "authorization_pending"})
            return self.reply(200, {"access_token": "M-ACCESS", "refresh_token": "M-REFRESH", "expires_in": 3600})
        if self.path == "/drive/v3/files":
            self.body()
            return self.reply(200, {"id": "FOLDER"})
        if self.path.startswith("/upload/drive/v3/files"):
            meta = json.loads(self.body())
            assert meta["parents"] == ["FOLDER"]
            sid = str(len(FakeCloud.sessions) + 1)
            FakeCloud.sessions[sid] = meta["name"]
            return self.reply(200, {}, headers={"Location": f"{base}/upload-session/g/{sid}"})
        m = re.match(r"^/graph/me/drive/special/approot:/(.+):/createUploadSession$", self.path)
        if m:
            self.body()
            sid = "m" + str(len(FakeCloud.sessions) + 1)
            FakeCloud.sessions[sid] = urllib.parse.unquote(m.group(1))
            return self.reply(200, {"uploadUrl": f"{base}/upload-session/m/{sid}"})
        self.reply(404)


class RemoteBackupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeCloud)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        fb = f"http://127.0.0.1:{cls.fake.server_address[1]}"
        cls.saved = (remote_storage.GOOGLE_OAUTH, remote_storage.GOOGLE_API, remote_storage.MS_LOGIN, remote_storage.MS_GRAPH)
        remote_storage.GOOGLE_OAUTH, remote_storage.GOOGLE_API = fb + "/oauth", fb
        remote_storage.MS_LOGIN, remote_storage.MS_GRAPH = fb + "/ms", fb + "/graph"
        remote_storage.GoogleDrive.token_url = fb + "/oauth/token"
        remote_storage.OneDrive.token_url = fb + "/ms/token"
        cls.fb = fb
        cls.work = tempfile.mkdtemp()
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.srv = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        cls.ctx.db.set_setting("server_name", "Salon de Colas")

    @classmethod
    def tearDownClass(cls):
        (remote_storage.GOOGLE_OAUTH, remote_storage.GOOGLE_API, remote_storage.MS_LOGIN, remote_storage.MS_GRAPH) = cls.saved
        cls.srv.shutdown()
        cls.fake.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_01_encryption(self):
        src = os.path.join(self.work, "plain.bin")
        data = os.urandom(3 * 1024 * 1024 + 17)
        with open(src, "wb") as f:
            f.write(data)
        enc, out = src + ".enc", src + ".out"
        backup.encrypt_file(src, enc, "une phrase secrète")
        self.assertTrue(backup.is_encrypted(enc))
        with open(enc, "rb") as f:
            self.assertNotIn(data[:64], f.read())
        backup.decrypt_file(enc, out, "une phrase secrète")
        with open(out, "rb") as f:
            self.assertEqual(f.read(), data)
        with self.assertRaises(backup.BackupError):
            backup.decrypt_file(enc, out, "mauvaise phrase")
        with open(enc, "r+b") as f:  # un octet modifié : refusé
            f.seek(100)
            b = f.read(1)
            f.seek(100)
            f.write(bytes([b[0] ^ 1]))
        with self.assertRaises(backup.BackupError):
            backup.decrypt_file(enc, out, "une phrase secrète")

    def test_02_named_after_server(self):
        b = backup.create(self.ctx, distribute=False)
        self.assertTrue(b["name"].startswith("nexis-salon-de-colas_"), b["name"])
        self.assertEqual((b["server"], b["mine"]), ("Salon de Colas", True))
        self.assertTrue(backup.NAME.match("nexis-20260101-000000-auto.tar.gz"))  # anciens noms toujours reconnus
        self.assertEqual(backup.slug("Été à Nîmes !"), "ete-a-nimes")

    def add_remote(self, kind, conf, name=None):
        st, r = self.admin.call("POST", "/api/admin/backups/remotes", {"kind": kind, "conf": conf, "name": name, "keep": 2})
        self.assertEqual(st, 200, r)
        dumped = json.dumps(r)
        for v in ("x" * 40, "GOCSPX-secret", "G-REFRESH", "M-REFRESH"):
            self.assertNotIn(v, dumped)  # les secrets ne reviennent jamais à l'interface
        return r

    def link(self, rid):
        st, c = self.admin.call("POST", f"/api/admin/backups/remotes/{rid}/link")
        self.assertEqual(st, 200, c)
        self.assertEqual(self.admin.call("POST", f"/api/admin/backups/remotes/{rid}/poll")[1]["status"], "pending")
        self.assertEqual(self.admin.call("POST", f"/api/admin/backups/remotes/{rid}/poll")[1]["status"], "linked")
        return c

    def test_03_storages(self):
        st, r = self.admin.call("POST", "/api/admin/backups/remotes", {"kind": "s3", "conf": {
            "endpoint": self.fb + "/s3", "region": "eu-west-3", "bucket": "inconnu", "access_key": "AKIATEST", "secret_key": "x" * 40}})
        self.assertEqual(st, 400)  # vérifié à l'ajout : compartiment introuvable
        s3 = self.add_remote("s3", {"endpoint": self.fb + "/s3", "region": "eu-west-3", "bucket": "sauvegardes",
                                    "prefix": "nexis", "access_key": "AKIATEST", "secret_key": "x" * 40})
        g = self.add_remote("gdrive", {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-secret"})
        self.assertFalse(g["linked"])
        c = self.link(g["id"])
        self.assertEqual(c["user_code"], "GOOG-123")
        m = self.add_remote("onedrive", {"client_id": "11111111-2222-3333-4444-555555555555"})
        self.assertEqual(self.link(m["id"])["user_code"], "MS-456")
        # phrase secrète : les copies envoyées hors du serveur sont chiffrées
        self.assertEqual(self.admin.call("PUT", "/api/admin/backups/settings", {"passphrase": "court"})[0], 400)
        st, v = self.admin.call("PUT", "/api/admin/backups/settings", {"passphrase": "phrase très secrète"})
        self.assertTrue(v["passphrase_set"])
        b = backup.create(self.ctx, "auto", distribute=False)
        results = backup.distribute(self.ctx, os.path.join(backup.directory(self.ctx), b["name"]))
        self.assertEqual(sorted(ok for _, ok in results), [True, True, True], results)
        names = sorted(FakeCloud.objects)
        self.assertEqual(names, sorted([f"s3:nexis/{b['name']}.enc", f"g:{b['name']}.enc", f"m:{b['name']}.enc"]))
        self.assertTrue(all(v.startswith(backup.MAGIC) for v in FakeCloud.objects.values()))
        self.assertTrue(any("Credential=AKIATEST/" in a and "/eu-west-3/s3/aws4_request" in a for a in FakeCloud.auth_seen))
        for r in (s3, g, m):
            st, files = self.admin.call("GET", f"/api/admin/backups/remotes/{r['id']}/files")
            self.assertEqual(st, 200, files)
            self.assertEqual([(f["name"], f["encrypted"], f["server"]) for f in files["files"]],
                             [(b["name"] + ".enc", True, "salon-de-colas")])
        # au-delà du nombre gardé (2), les plus anciennes copies automatiques d'ici sont retirées
        for _ in range(2):
            time.sleep(1.1)
            nb = backup.create(self.ctx, "auto", distribute=False)
            backup.distribute(self.ctx, os.path.join(backup.directory(self.ctx), nb["name"]))
        self.assertEqual(sum(1 for k in FakeCloud.objects if k.startswith("s3:")), 2)
        self.assertNotIn(f"s3:nexis/{b['name']}.enc", FakeCloud.objects)
        # restaurer depuis Google Drive : téléchargée, déchiffrée, mise en place au redémarrage
        st, files = self.admin.call("GET", f"/api/admin/backups/remotes/{g['id']}/files")
        st, r = self.admin.call("POST", f"/api/admin/backups/remotes/{g['id']}/restore", {"id": files["files"][0]["id"]})
        self.assertEqual((st, r["server"]), (200, "Salon de Colas"), r)
        self.assertTrue(os.path.isfile(os.path.join(self.ctx.data_dir, backup.PENDING, "seance.db")))
        shutil.rmtree(os.path.join(self.ctx.data_dir, backup.PENDING))
        st, lst = self.admin.call("GET", "/api/admin/backups/remotes")
        self.assertTrue(all(x["last"]["ok"] for x in lst["remotes"]))
        for r in (s3, g, m):
            self.assertEqual(self.admin.call("DELETE", f"/api/admin/backups/remotes/{r['id']}")[0], 200)

    def test_04_restore_from_file_and_path(self):
        b = backup.create(self.ctx, distribute=False)
        path = os.path.join(backup.directory(self.ctx), b["name"])
        enc = os.path.join(self.work, "copie.tar.gz.enc")
        backup.encrypt_file(path, enc, "autre phrase secrète")
        with open(enc, "rb") as f:
            data = f.read()
        st, r = self.admin.call("POST", "/api/admin/backups/restore-file", raw_body=data)
        self.assertEqual(st, 400)
        self.assertIn("Phrase secrète incorrecte", r["error"])  # la phrase enregistrée n'est pas la bonne
        st, r = self.admin.call("POST", "/api/admin/backups/restore-file", raw_body=data,
                                headers={"X-Backup-Passphrase": "autre phrase secrète"})
        self.assertEqual((st, r["restarting"]), (200, False), r)
        shutil.rmtree(os.path.join(self.ctx.data_dir, backup.PENDING))
        outside = os.path.join(self.work, "disque-externe")
        os.makedirs(outside)
        shutil.copy(path, os.path.join(outside, "ma-sauvegarde.tar.gz"))
        st, r = self.admin.call("POST", "/api/admin/backups/restore-path", {"path": os.path.join(outside, "ma-sauvegarde.tar.gz")})
        self.assertEqual(st, 200, r)
        self.assertTrue(os.path.isfile(os.path.join(outside, "ma-sauvegarde.tar.gz")))  # l'original reste en place
        shutil.rmtree(os.path.join(self.ctx.data_dir, backup.PENDING))
        st, r = self.admin.call("POST", "/api/admin/backups/restore-path", {"path": "/etc/passwd"})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
