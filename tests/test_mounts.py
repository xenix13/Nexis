"""Tests du service de montage SMB avec de faux mount / smbclient."""
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAKE_SMBCLIENT = r'''#!/usr/bin/env python3
import sys
args = sys.argv[1:]
auth = args[args.index("-A") + 1]
cred = dict(l.split(" = ", 1) for l in open(auth).read().splitlines() if " = " in l)
if cred.get("password") != "secret":
    print("session setup failed: NT_STATUS_LOGON_FAILURE", file=sys.stderr); sys.exit(1)
if "-L" in args:
    print("Disk|Films|Mes films\nDisk|Series|\nIPC|IPC$|IPC Service\nDisk|ADMIN$|\nDisk|Animes|Japanimation")
    sys.exit(0)
target = args[0]
if not target.endswith(("/Films", "/Series", "/Animes", "/Mes Films")):
    print("tree connect failed: NT_STATUS_BAD_NETWORK_NAME", file=sys.stderr); sys.exit(1)
print("  .  D  0  Thu Oct 22 2026")
'''

FAKE_MOUNT = r'''#!/usr/bin/env python3
import os, re, sys
path = sys.argv[-1]
fstab = open(os.environ["SEANCE_FSTAB"]).read().splitlines()
line = next((l for l in fstab if not l.startswith("#") and len(l.split()) > 1 and l.split()[1] == path.replace(" ", "\\040")), None)
if not line:
    print("mount: can't find in fstab", file=sys.stderr); sys.exit(1)
if os.environ.get("FAKE_EPERM"):
    print("mount error(1): Operation not permitted", file=sys.stderr); sys.exit(32)
cred = re.search(r"credentials=([^,]+)", line).group(1)
pw = dict(l.split("=", 1) for l in open(cred).read().splitlines())["password"]
if pw != "secret":
    print("mount error(13): Permission denied", file=sys.stderr); sys.exit(32)
with open(os.environ["SEANCE_MOUNTINFO"], "a") as f:
    f.write(f"36 35 0:50 / {path.replace(' ', chr(92) + '040')} ro - cifs x ro\n")
'''

FAKE_UMOUNT = r'''#!/usr/bin/env python3
import os, sys
path = sys.argv[-1].replace(" ", "\\040")
mi = os.environ["SEANCE_MOUNTINFO"]
lines = [l for l in open(mi).read().splitlines() if l.split()[4] != path]
open(mi, "w").write("".join(l + "\n" for l in lines))
'''


class MountdTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        bin_dir = os.path.join(cls.dir, "bin")
        os.makedirs(bin_dir)
        for name, body in (("smbclient", FAKE_SMBCLIENT), ("mount", FAKE_MOUNT), ("umount", FAKE_UMOUNT),
                           ("mount.cifs", "#!/bin/sh\nexit 0\n")):
            p = os.path.join(bin_dir, name)
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        cls.env_backup = dict(os.environ)
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]
        os.environ["SEANCE_FSTAB"] = cls.fstab = os.path.join(cls.dir, "fstab")
        os.environ["SEANCE_MOUNTINFO"] = cls.mountinfo = os.path.join(cls.dir, "mountinfo")
        os.environ["SEANCE_SMB_CRED_DIR"] = cls.creds = os.path.join(cls.dir, "smb")
        os.environ["SEANCE_MOUNT_ROOT"] = cls.root = os.path.join(cls.dir, "mnt")
        os.environ["SEANCE_MOUNTD_SOCK"] = cls.sock = os.path.join(cls.dir, "run", "mountd.sock")
        with open(cls.fstab, "w") as f:
            f.write("UUID=abc / ext4 defaults 0 1\n")
        open(cls.mountinfo, "w").close()
        import importlib
        from seance import mountd
        cls.m = importlib.reload(mountd)
        cls.mounts = cls.m.Mounts()
        cls.mounts.env["unprivileged"] = False
        cls.server = cls.m.make_server(cls.mounts, cls.sock, allowed_uids={os.getuid()})
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        os.environ.clear()
        os.environ.update(cls.env_backup)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def call(self, cmd, **args):
        return self.m.call(cmd, args, sock_path=self.sock)

    def test_1_discover(self):
        r = self.call("discover", host="192.168.1.5", username="jellyfin", password="secret")
        self.assertEqual([s["name"] for s in r["shares"]], ["Animes", "Films", "Series"])
        with self.assertRaisesRegex(self.m.MountError, "mot de passe refusé"):
            self.call("discover", host="192.168.1.5", username="jellyfin", password="nope")
        with self.assertRaisesRegex(self.m.MountError, "Adresse du NAS invalide"):
            self.call("discover", host="192.168.1.5; rm -rf /", username="x", password="y")

    def test_2_add_status_remove(self):
        r = self.call("add", name="films", host="192.168.1.5", share="Films", username="jellyfin", password="secret")
        path = os.path.join(self.root, "films")
        self.assertEqual(r["path"], path)
        fstab = open(self.fstab).read()
        self.assertIn("# seance-smb:films\n//192.168.1.5/Films " + path + " cifs credentials=", fstab)
        self.assertIn(",ro,", fstab)
        self.assertTrue(fstab.startswith("UUID=abc / ext4"))  # lignes existantes conservées
        cred = os.path.join(self.creds, "films.cred")
        self.assertEqual(oct(os.stat(cred).st_mode & 0o777), "0o600")
        self.assertNotIn("secret", fstab)
        st = self.call("status")
        self.assertEqual([(s["name"], s["mounted"], s["username"]) for s in st["shares"]], [("films", True, "jellyfin")])
        self.assertNotIn("password", json.dumps(st))
        # mauvais partage : refusé avant d'écrire quoi que ce soit
        before = open(self.fstab).read()
        with self.assertRaisesRegex(self.m.MountError, "n'existe pas"):
            self.call("add", name="x", host="192.168.1.5", share="Nope", username="jellyfin", password="secret")
        self.assertEqual(open(self.fstab).read(), before)
        # partage avec espace
        self.call("add", name="mes-films", host="nas.local", share="Mes Films", username="jellyfin", password="secret")
        self.assertIn("//nas.local/Mes\\040Films", open(self.fstab).read())
        # modification sans retaper le mot de passe
        self.call("add", name="films", host="192.168.1.5", share="Films", username="jellyfin", password="", version="3.0")
        self.assertIn("vers=3.0", open(self.fstab).read())
        self.assertEqual(open(self.fstab).read().count("# seance-smb:films\n"), 1)
        self.call("remove", name="films")
        self.call("remove", name="mes-films")
        self.assertEqual(open(self.fstab).read(), "UUID=abc / ext4 defaults 0 1\n")
        self.assertFalse(os.path.exists(cred))
        self.assertEqual(open(self.mountinfo).read().strip(), "")

    def test_3_mount_refused_rolls_back(self):
        os.environ["FAKE_EPERM"] = "1"
        self.mounts.env["unprivileged"] = True
        try:
            before = open(self.fstab).read()
            with self.assertRaisesRegex(self.m.MountError, "non privilégié"):
                self.call("add", name="series", host="192.168.1.5", share="Series", username="jellyfin", password="secret")
            self.assertEqual(open(self.fstab).read(), before)
            self.assertFalse(os.path.exists(os.path.join(self.creds, "series.cred")))
        finally:
            del os.environ["FAKE_EPERM"]
            self.mounts.env["unprivileged"] = False

    def test_4_validation_and_remount(self):
        for bad in ({"name": "../etc"}, {"name": "Films"}, {"share": "a/b"}, {"subdir": "../.."},
                    {"username": "a\nb"}, {"host": "-oProxyCommand"}):
            args = dict(name="ok", host="192.168.1.5", share="Films", username="u", password="secret", **{})
            args.update(bad)
            with self.assertRaises(self.m.MountError, msg=str(bad)):
                self.mounts.validate(args)
        self.call("add", name="anim", host="192.168.1.5", share="Animes", username="jellyfin", password="secret")
        open(self.mountinfo, "w").close()  # le NAS a redémarré : plus monté
        self.assertFalse(self.call("status")["shares"][0]["mounted"])
        self.mounts.ensure_all()
        self.assertTrue(self.call("status")["shares"][0]["mounted"])
        self.call("remount", name="anim")
        self.call("remove", name="anim")

    def test_5_peer_check(self):
        other = self.m.make_server(self.mounts, self.sock + "2", allowed_uids={99999})
        threading.Thread(target=other.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(self.m.MountError):
                self.m.call("status", sock_path=self.sock + "2")
        finally:
            other.shutdown()


if __name__ == "__main__":
    unittest.main()
