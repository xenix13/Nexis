"""Photothèque (albums, EXIF, visionneuse) et serveur DLNA (annonce, navigation, lecture).

python3 -m unittest tests.test_photos_dlna
"""
import http.client
import urllib.parse
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET

from tests.test_api import STATIC, Client, media_root, wait_scan

from seance import dlna as dlna_mod
from seance.api import Ctx, build_app
from seance.photos import read_info
from seance.web import Server, make_handler

DIDL = "{urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/}"
DC = "{http://purl.org/dc/elements/1.1/}"


def exif_block(orientation, taken, model):
    """Segment APP1 EXIF minimal : orientation et appareil (IFD0), date de prise de vue (sous-IFD EXIF)."""
    model_b = model.encode() + b"\0"
    date_b = taken.encode() + b"\0"
    # en-tête TIFF (8) + IFD0 : 3 entrées (2 + 36 + 4) = 42 -> données à partir de 50
    model_off = 8 + 2 + 3 * 12 + 4
    sub_off = model_off + len(model_b)
    date_off = sub_off + 2 + 12 + 4
    t = b"MM\x00\x2a" + struct.pack(">I", 8)
    t += struct.pack(">H", 3)
    t += struct.pack(">HHI", 0x0110, 2, len(model_b)) + struct.pack(">I", model_off)
    t += struct.pack(">HHI", 0x0112, 3, 1) + struct.pack(">HH", orientation, 0)
    t += struct.pack(">HHI", 0x8769, 4, 1) + struct.pack(">I", sub_off)
    t += struct.pack(">I", 0) + model_b
    t += struct.pack(">H", 1) + struct.pack(">HHI", 0x9003, 2, len(date_b)) + struct.pack(">I", date_off)
    t += struct.pack(">I", 0) + date_b
    payload = b"Exif\0\0" + t
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def make_jpeg(path, w, h, orientation=None, taken=None, model="Pixel 8", color="red"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}", "-frames:v", "1",
                    path], check=True)
    if orientation:
        with open(path, "rb") as f:
            data = f.read()
        with open(path, "wb") as f:
            f.write(data[:2] + exif_block(orientation, taken, model) + data[2:])


def jpeg_size(data):
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.write(data)
    tmp.close()
    try:
        info = read_info(tmp.name)
        return info.get("w"), info.get("h")
    finally:
        os.remove(tmp.name)


class PhotosDlnaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        cls.pics = p = os.path.join(cls.work, "photos")
        os.makedirs(os.path.join(p, "Vacances 2024", "Plage"))
        make_jpeg(os.path.join(p, "Vacances 2024", "IMG_0001.jpg"), 64, 32, 6, "2024:07:14 10:30:00", color="blue")
        make_jpeg(os.path.join(p, "Vacances 2024", "IMG_0002.jpg"), 64, 32, 1, "2024:07:15 18:00:00")
        make_jpeg(os.path.join(p, "Vacances 2024", "Plage", "IMG_0003.jpg"), 48, 48, 1, "2024:07:16 09:00:00")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=green:s=40x30", "-frames:v", "1",
                        os.path.join(p, "capture.png")], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10", "-t", "2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-metadata", "creation_time=2024-07-15T12:00:00Z",
                        os.path.join(p, "Vacances 2024", "VID_0001.mp4")], check=True)
        open(os.path.join(p, "notes.txt"), "w").close()
        os.makedirs(os.path.join(p, "Vide"))
        old = time.time() - 3600
        for d, _, files in os.walk(p):
            for f in files:
                os.utime(os.path.join(d, f), (old, old))
        cls.ctx = Ctx(os.path.join(cls.work, "data"), STATIC)
        cls.server = Server(("127.0.0.1", 0), make_handler(build_app(cls.ctx)))
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.admin = Client(cls.base)
        st, r = cls.admin.call("POST", "/api/setup", {"username": "admin", "password": "motdepasse1"},
                               headers={"X-Setup-Code": cls.ctx.setup_code})
        assert st == 200, r
        for name, kind, folder in (("Films", "movie", os.path.join(media_root(), "films")), ("Photos", "photo", p)):
            st, r = cls.admin.call("POST", "/api/admin/libraries", {"name": name, "kind": kind, "paths": [folder]})
            assert st == 200, r
            wait_scan(cls.admin)
        st, libs = cls.admin.call("GET", "/api/libraries")
        cls.libs = {l["title"]: l for l in libs}

    @classmethod
    def tearDownClass(cls):
        cls.ctx.dlna.stop()
        cls.server.shutdown()
        shutil.rmtree(cls.work, ignore_errors=True)

    # ---------------------------------------------------------------- photothèque
    def test_01_albums_and_exif(self):
        lib = self.libs["Photos"]
        self.assertEqual(lib["type"], "photo")
        st, r = self.admin.call("GET", f"/api/libraries/{lib['id']}/photos")
        self.assertEqual(st, 200, r)
        self.assertEqual([(a["title"], a["count"]) for a in r["albums"]], [("Vacances 2024", 4)])  # « Vide » écarté
        self.assertEqual([p["title"] for p in r["photos"]], ["capture"])
        self.assertEqual((r["photos"][0]["w"], r["photos"][0]["h"]), (40, 30))
        st, a = self.admin.call("GET", f"/api/libraries/{lib['id']}/photos?album={r['albums'][0]['id']}")
        self.assertEqual([x["title"] for x in a["albums"]], ["Plage"])
        self.assertEqual([x["title"] for x in a["photos"]], ["IMG_0001", "VID_0001", "IMG_0002"])  # par date de prise de vue
        p1 = a["photos"][0]
        self.assertEqual((p1["taken"], p1["w"], p1["h"], p1["camera"]), ("2024-07-14T10:30:00", 32, 64, "Pixel 8"))
        self.assertEqual([x["title"] for x in a["path"]], ["Vacances 2024"])
        st, t = self.admin.call("GET", f"/api/libraries/{lib['id']}/photos?view=timeline")
        self.assertEqual(t["total"], 5)
        self.assertEqual([x["title"] for x in t["photos"]], ["capture", "IMG_0003", "IMG_0002", "VID_0001", "IMG_0001"])  # sans EXIF : date du fichier
        # vignettes (photo, album) et image de la visionneuse remise à l'endroit
        st, data, _ = self.admin.call("GET", f"/api/img?p={p1['thumb']}&w=100&h=100", raw=True)
        self.assertEqual((st, data[:2]), (200, b"\xff\xd8"))
        st, data, _ = self.admin.call("GET", f"/api/img?p={r['albums'][0]['thumb']}&w=100&h=100", raw=True)
        self.assertEqual((st, data[:2]), (200, b"\xff\xd8"))
        st, data, _ = self.admin.call("GET", f"/api/photos/{p1['id']}/view?w=1280", raw=True)
        self.assertEqual(st, 200)
        self.assertEqual(jpeg_size(data), (32, 64))  # orientation 6 : portrait, et jamais agrandie
        st, data, h = self.admin.call("GET", f"/api/photos/{p1['id']}/original", raw=True)
        self.assertEqual(st, 200)
        with open(os.path.join(self.pics, "Vacances 2024", "IMG_0001.jpg"), "rb") as f:
            self.assertEqual(data, f.read())
        self.assertIn("attachment", h.get("Content-Disposition", ""))

    def test_02_photos_stay_apart(self):
        st, home = self.admin.call("GET", "/api/home")
        self.assertNotIn("Photos", [r["library"]["title"] for r in home.get("rows", [])])
        st, res = self.admin.call("GET", "/api/search?q=img")
        self.assertEqual(sum(len(v) for v in res.values() if isinstance(v, list)), 0)
        # un compte sans accès à la photothèque ne voit rien
        st, r = self.admin.call("POST", "/api/admin/users", {"username": "tom", "password": "motdepasseT",
                                                              "all_libraries": False, "libraries": [self.libs["Films"]["id"]]})
        self.assertEqual(st, 200, r)
        tom = Client(self.base)
        tom.call("POST", "/api/login", {"username": "tom", "password": "motdepasseT"})
        self.assertEqual(tom.call("GET", f"/api/libraries/{self.libs['Photos']['id']}/photos")[0], 404)
        pid = self.ctx.db.scalar("SELECT id FROM items WHERE type='photo' LIMIT 1")
        self.assertEqual(tom.call("GET", f"/api/photos/{pid}/view")[0], 404)
        self.assertEqual(tom.call("GET", f"/api/photos/{pid}/original")[0], 404)

    def test_03_rescan_removes_and_updates(self):
        lib = self.libs["Photos"]
        extra = os.path.join(self.pics, "Noël", "IMG_9.jpg")
        os.makedirs(os.path.dirname(extra))
        make_jpeg(extra, 20, 20)
        old = time.time() - 3600
        os.utime(extra, (old, old))
        self.admin.call("POST", "/api/admin/scan", {"library": lib["id"]})
        wait_scan(self.admin)
        st, r = self.admin.call("GET", f"/api/libraries/{lib['id']}/photos")
        self.assertEqual([a["title"] for a in r["albums"]], ["Noël", "Vacances 2024"])
        shutil.rmtree(os.path.dirname(extra))
        self.admin.call("POST", "/api/admin/scan", {"library": lib["id"]})
        wait_scan(self.admin)
        st, r = self.admin.call("GET", f"/api/libraries/{lib['id']}/photos")
        self.assertEqual([a["title"] for a in r["albums"]], ["Vacances 2024"])

    # ---------------------------------------------------------------- DLNA
    def soap(self, path, service, action, args):
        body = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                f'<u:{action} xmlns:u="{service}">' + "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
                + f"</u:{action}></s:Body></s:Envelope>")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request("POST", path, body=body.encode(), headers={"Content-Type": 'text/xml; charset="utf-8"',
                                                             "SOAPACTION": f'"{service}#{action}"'})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def browse(self, oid, flag="BrowseDirectChildren", start=0, count=0):
        st, data = self.soap("/dlna/control/cd", dlna_mod.CD, "Browse",
                             {"ObjectID": oid, "BrowseFlag": flag, "Filter": "*", "StartingIndex": start,
                              "RequestedCount": count, "SortCriteria": ""})
        if st != 200:
            return st, None, None
        root = ET.fromstring(data)
        result = next(e for e in root.iter() if e.tag == "Result").text
        total = int(next(e for e in root.iter() if e.tag == "TotalMatches").text)
        return st, ET.fromstring(result), total

    def raw(self, method, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        c.request(method, path, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data, r

    def test_10_dlna(self):
        self.assertEqual(self.raw("GET", "/dlna/description.xml")[0], 404)  # désactivé par défaut
        self.ctx.dlna.http_port = self.port
        st, v = self.admin.call("PUT", "/api/admin/dlna", {"enabled": True, "name": "Salon",
                                                            "libraries": [self.libs["Films"]["id"], self.libs["Photos"]["id"]]})
        self.assertEqual(st, 200, v)
        self.assertTrue(v["enabled"])
        st, desc, _ = self.raw("GET", "/dlna/description.xml")
        self.assertEqual(st, 200)
        self.assertIn(b"<friendlyName>Salon</friendlyName>", desc)
        self.assertIn(b"urn:schemas-upnp-org:device:MediaServer:1", desc)
        self.assertEqual(self.raw("GET", "/dlna/cd.xml")[0], 200)
        # racine : les deux bibliothèques
        st, didl, total = self.browse("0")
        self.assertEqual(st, 200)
        names = [c.find(DC + "title").text for c in didl.findall(DIDL + "container")]
        self.assertEqual(sorted(names), ["Films", "Photos"])
        films = next(c.get("id") for c in didl.findall(DIDL + "container") if c.find(DC + "title").text == "Films")
        st, didl, total = self.browse(films)
        items = didl.findall(DIDL + "item")
        self.assertEqual(len(items), total)
        self.assertGreater(total, 0)
        res = items[0].find(DIDL + "res")
        self.assertIn("http-get:*:video/", res.get("protocolInfo"))
        url = res.text
        self.assertTrue(url.startswith(f"http://127.0.0.1:{self.port}/dlna/media/"), url)
        # lecture par morceaux, telle qu'un téléviseur la fait
        st, data, r = self.raw("GET", url.split(str(self.port), 1)[1], {"Range": "bytes=0-99", "getcontentFeatures.dlna.org": "1"})
        self.assertEqual((st, len(data)), (206, 100))
        self.assertEqual(r.getheader("transferMode.dlna.org"), "Streaming")
        st, _, r = self.raw("HEAD", url.split(str(self.port), 1)[1])
        self.assertEqual(st, 200)
        self.assertGreater(int(r.getheader("Content-Length")), 100)
        # photos : albums, puis photos en JPEG (et l'original)
        photos = next(c.get("id") for c in self.browse("0")[1].findall(DIDL + "container") if c.find(DC + "title").text == "Photos")
        st, didl, total = self.browse(photos)
        self.assertEqual([c.find(DC + "title").text for c in didl.findall(DIDL + "container")], ["Vacances 2024"])
        self.assertEqual([c.find(DC + "title").text for c in didl.findall(DIDL + "item")], ["capture"])
        album = didl.find(DIDL + "container").get("id")
        st, didl, total = self.browse(album)
        self.assertEqual(total, 4)  # sous-album, 2 photos, 1 vidéo
        clip = next(c for c in didl.findall(DIDL + "item") if c.find(DC + "title").text == "VID_0001")
        self.assertEqual(clip.find("{urn:schemas-upnp-org:metadata-1-0/upnp/}class").text, "object.item.videoItem")
        self.assertIn("video/mp4", clip.find(DIDL + "res").get("protocolInfo"))
        photo = didl.findall(DIDL + "item")[0]
        self.assertEqual(photo.find("{urn:schemas-upnp-org:metadata-1-0/upnp/}class").text, "object.item.imageItem.photo")
        jpg = photo.findall(DIDL + "res")[0].text
        st, data, r = self.raw("GET", jpg.split(str(self.port), 1)[1])
        self.assertEqual((st, data[:2], r.getheader("Content-Type")), (200, b"\xff\xd8", "image/jpeg"))
        st, didl, total = self.browse(album, "BrowseMetadata")
        self.assertEqual((st, total, didl.find(DIDL + "container").get("id")), (200, 1, album))
        thumb = self.raw("GET", f"/dlna/thumb/{album[1:]}.jpg")
        self.assertEqual((thumb[0], thumb[1][:2]), (200, b"\xff\xd8"))
        self.assertEqual(self.browse("I999999")[0], 500)  # objet inconnu : erreur UPnP
        # bibliothèque retirée du partage : plus rien n'en sort
        self.admin.call("PUT", "/api/admin/dlna", {"libraries": [self.libs["Films"]["id"]]})
        st, didl, total = self.browse("0")
        self.assertEqual([c.find(DC + "title").text for c in didl.findall(DIDL + "container")], ["Films"])
        self.assertEqual(self.browse(album)[0], 500)
        self.assertEqual(self.raw("GET", jpg.split(str(self.port), 1)[1])[0], 404)
        # abonnement aux événements
        st, _, r = self.raw("SUBSCRIBE", "/dlna/event/cd", {"CALLBACK": "<http://127.0.0.1:9/>", "NT": "upnp:event"})
        self.assertEqual(st, 200)
        self.assertTrue(r.getheader("SID").startswith("uuid:"))

    def test_11_dlna_ssdp(self):
        self.ctx.dlna.http_port = self.port
        self.admin.call("PUT", "/api/admin/dlna", {"enabled": True})
        st, v = self.admin.call("GET", "/api/admin/dlna")
        if not v["running"]:
            self.skipTest(f"port SSDP indisponible ici : {v['error']}")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        s.sendto(b"M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 1\r\n"
                 b"ST: urn:schemas-upnp-org:device:MediaServer:1\r\n\r\n", ("127.0.0.1", 1900))
        data, _ = s.recvfrom(4096)
        s.close()
        text = data.decode()
        self.assertTrue(text.startswith("HTTP/1.1 200 OK"), text)
        self.assertIn("ST: urn:schemas-upnp-org:device:MediaServer:1", text)
        loc = re.search(r"LOCATION: (\S+)", text).group(1)
        self.assertTrue(loc.endswith(f":{self.port}/dlna/description.xml"), loc)
        # désactivé : plus d'annonce ni de réponse
        self.admin.call("PUT", "/api/admin/dlna", {"enabled": False})
        self.assertEqual(self.raw("GET", "/dlna/description.xml")[0], 404)

    # ---------------------------------------------------------------- qualité automatique
    def test_20_auto_quality(self):
        from seance.transcoder import auto_quality
        p4k = {"bitrate": 40_000_000, "video": {"height": 2160}}
        self.assertEqual(auto_quality(p4k, None), "original")       # pas de mesure : l'original
        self.assertEqual(auto_quality(p4k, 100_000), "original")    # réseau local rapide
        self.assertEqual(auto_quality(p4k, 20_000), "1080p")
        self.assertEqual(auto_quality(p4k, 6_000), "720p")
        self.assertEqual(auto_quality(p4k, 800), "480p")            # jamais en dessous de 480p
        p720 = {"bitrate": 9_000_000, "video": {"height": 720}}
        self.assertEqual(auto_quality(p720, 12_000), "original")
        self.assertEqual(auto_quality(p720, 11_000), "720p")         # pas de « 1080p » pour un fichier 720p
        p720hi = {"bitrate": 20_000_000, "video": {"height": 720}}
        self.assertEqual(auto_quality(p720hi, 12_000), "1080p")      # 720p très lourd : le palier 1080p réduit le débit
        size_only = {"video": {"height": 1080}}  # débit d'après la taille : 3 Go en 1 h = 6,7 Mbit/s
        self.assertEqual(auto_quality(size_only, 9_000, 3_000_000_000, 3_600_000), "original")
        self.assertEqual(auto_quality(size_only, 8_000, 3_000_000_000, 3_600_000), "720p")
        # mesure du débit : octets incompressibles, taille bornée
        st, data, h = self.admin.call("GET", "/api/bandwidth?size=70000", raw=True)
        self.assertEqual((st, len(data), h.get("Cache-Control")), (200, 70000, "no-store"))
        st, data, h = self.admin.call("GET", "/api/bandwidth?size=1", raw=True)
        self.assertEqual(len(data), 1 << 16)
        # lecture en automatique : débit faible -> conversion, débit large -> l'original
        movie = self.ctx.db.one("SELECT id, probe FROM items WHERE type='movie' ORDER BY id LIMIT 1")
        caps = {"video": ["h264", "hevc"], "audio": ["aac", "ac3", "mp3"], "containers": ["mp4", "matroska"], "native": True}
        st, r = self.admin.call("POST", "/api/play", {"id": str(movie["id"]), "quality": "auto", "bandwidth": 900, "caps": caps})
        self.assertEqual(st, 200, r)
        # (fichier de test en 180p à 0,8 Mbit/s : en 480p, rien à réduire, il reste lu tel quel)
        self.assertEqual((r["quality"], r["auto_quality"], r["bandwidth"]), ("auto", "480p", 900))
        self.admin.call("POST", "/api/stop", {"stream": r["stream"]})
        st, r = self.admin.call("POST", "/api/play", {"id": str(movie["id"]), "quality": "auto", "bandwidth": 500_000, "caps": caps})
        self.assertEqual((r["auto_quality"], r["mode"]), ("original", "direct"))
        self.admin.call("POST", "/api/stop", {"stream": r["stream"]})
        # sans mesure dans la demande : la dernière de cet appareil (même compte, même adresse)
        self.admin.call("POST", "/api/play", {"id": str(movie["id"]), "quality": "auto", "bandwidth": 900, "caps": caps})
        st, r = self.admin.call("POST", "/api/play", {"id": str(movie["id"]), "quality": "auto", "caps": caps})
        self.assertEqual((r["auto_quality"], r["bandwidth"]), ("480p", 900))
        self.admin.call("POST", "/api/stop", {"stream": r["stream"]})
        # qualité par défaut d'un compte : automatique
        st, me = self.admin.call("GET", "/api/me")
        self.assertEqual(me["prefs"]["quality"], "auto")

    def test_21_readrate_only_with_ffmpeg_6(self):
        """-readrate fige la préparation avec ffmpeg 8 : utilisé seulement avec ffmpeg 5 et 6."""
        from unittest import mock
        from seance import media
        with mock.patch.object(media, "has_option", return_value=True):
            for major, ok in ((6, True), (5, True), (7, False), (8, False), (0, False)):
                with mock.patch.object(media, "ffmpeg_major", return_value=major):
                    self.assertEqual(media.readrate_ok(), ok, major)

    def test_04_videos_favorites_and_share_links(self):
        lib = self.libs["Photos"]
        st, r = self.admin.call("GET", f"/api/libraries/{lib['id']}/photos")
        album = r["albums"][0]
        st, a = self.admin.call("GET", f"/api/libraries/{lib['id']}/photos?album={album['id']}")
        clip = next(p for p in a["photos"] if p["title"] == "VID_0001")
        self.assertEqual((clip["video"], clip["w"], clip["h"], clip["taken"]), (True, 160, 90, "2024-07-15T12:00:00"))
        self.assertGreater(clip["duration"], 1500)
        st, data, h = self.admin.call("GET", f"/api/photos/{clip['id']}/stream", headers={"Range": "bytes=0-99"}, raw=True)
        self.assertEqual((st, len(data), h["Content-Type"]), (206, 100, "video/mp4"))
        st, data, _ = self.admin.call("GET", f"/api/img?p={clip['thumb']}&w=100&h=100", raw=True)
        self.assertEqual((st, data[:2]), (200, b"\xff\xd8"))  # vignette : une image de la vidéo
        photo = next(p for p in a["photos"] if p["title"] == "IMG_0001")
        self.assertEqual(self.admin.call("GET", f"/api/photos/{photo['id']}/stream")[0], 404)
        # favoris
        st, r = self.admin.call("PUT", f"/api/photos/{photo['id']}/favorite", {"on": True})
        self.assertEqual((st, r["fav"]), (200, True))
        st, f = self.admin.call("GET", f"/api/libraries/{lib['id']}/photos?view=favorites")
        self.assertEqual([p["id"] for p in f["photos"]], [photo["id"]])
        self.assertTrue(f["photos"][0]["fav"])
        self.admin.call("PUT", f"/api/photos/{photo['id']}/favorite", {"on": False})
        # lien de partage de l'album « Plage » : sans compte, rien en dehors de l'album
        st, sub = self.admin.call("GET", f"/api/libraries/{lib['id']}/photos?album={album['id']}")
        plage = sub["albums"][0]
        st, share = self.admin.call("POST", f"/api/photos/albums/{plage['id']}/shares", {"days": 7})
        self.assertEqual(st, 200, share)
        token = share["token"]
        anon = Client(self.base)
        st, pub = anon.call("GET", f"/api/share/{token}")
        self.assertEqual((st, pub["title"], [p["title"] for p in pub["photos"]]), (200, "Plage", ["IMG_0003"]))
        pid = pub["photos"][0]["id"]
        st, data, _ = anon.call("GET", pub["photos"][0]["thumb_url"] + "&w=120", raw=True)
        self.assertEqual((st, data[:2]), (200, b"\xff\xd8"))
        self.assertEqual(anon.call("GET", f"/api/share/{token}/original/{pid}", raw=True)[0], 200)
        self.assertEqual(anon.call("GET", f"/api/share/{token}/img/{photo['id']}?k=view", raw=True)[0], 404)  # hors de l'album
        self.assertEqual(anon.call("GET", f"/api/share/{token}?album={album['id']}")[0], 404)  # pas au-dessus
        self.assertEqual(anon.call("GET", f"/api/photos/{pid}/view")[0], 401)  # l'API normale demande un compte
        st, lst = self.admin.call("GET", f"/api/photos/albums/{plage['id']}/shares")
        self.assertEqual([s["token"] for s in lst], [token])
        self.assertEqual(self.admin.call("POST", f"/api/photos/albums/{plage['id']}/shares", {"days": 365})[0], 400)
        self.admin.call("DELETE", f"/api/photos/shares/{token}")
        self.assertEqual(anon.call("GET", f"/api/share/{token}")[0], 404)  # révoqué

    def test_30_remote_control(self):
        """Télécommande : le lecteur s'annonce, envoie son état ; un autre appareil le voit et lui envoie des commandes."""
        cookie = "; ".join(f"{c.name}={c.value}" for c in self.admin.jar)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/remote/events?device=salon0001&name=TV%20du%20salon", headers={"Cookie": cookie})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        resp.fp.readline()
        st, r = self.admin.call("POST", "/api/remote/state", {"device": "salon0001", "state": {
            "title": "Brume", "position": 1000, "duration": 20000, "playing": True, "audio": [], "subtitles": []}})
        self.assertEqual((st, r["ok"]), (200, True))
        st, devs = self.admin.call("GET", "/api/remote/devices?exclude=telephone01")
        self.assertEqual([(d["id"], d["name"], d["state"]["title"]) for d in devs], [("salon0001", "TV du salon", "Brume")])
        st, r = self.admin.call("POST", "/api/remote/salon0001/cmd", {"action": "seekby", "value": 30000})
        self.assertEqual(st, 200, r)
        line = b""
        while not line.startswith(b"data: "):
            line = resp.fp.readline()
        self.assertEqual(json.loads(line[6:]), {"action": "seekby", "value": 30000})
        self.assertEqual(self.admin.call("POST", "/api/remote/salon0001/cmd", {"action": "rm -rf"})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/remote/inconnu01/cmd", {"action": "pause"})[0], 404)
        # un autre compte ne voit pas les appareils de celui-ci
        st, r = self.admin.call("POST", "/api/admin/users", {"username": "zoe", "password": "motdepasseZ"})
        zoe = Client(self.base)
        zoe.call("POST", "/api/login", {"username": "zoe", "password": "motdepasseZ"})
        self.assertEqual(zoe.call("GET", "/api/remote/devices")[1], [])
        self.assertEqual(zoe.call("POST", "/api/remote/salon0001/cmd", {"action": "pause"})[0], 404)
        conn.close()

    def test_31_push_notifications(self):
        from seance import push as push_mod
        st, k = self.admin.call("GET", "/api/push/key")
        self.assertEqual(st, 200)
        self.assertEqual(len(push_mod.base64.urlsafe_b64decode(k["key"] + "=")), 65)  # point P-256 non compressé
        # seuls les services push des navigateurs sont acceptés (pas d'adresse du réseau local)
        self.assertEqual(self.admin.call("POST", "/api/push/subscribe", {"endpoint": "https://192.168.1.10/x"})[0], 400)
        self.assertEqual(self.admin.call("POST", "/api/push/subscribe", {"endpoint": "http://fcm.googleapis.com/x"})[0], 400)
        st, r = self.admin.call("POST", "/api/push/subscribe", {"endpoint": "https://fcm.googleapis.com/fcm/send/abc"})
        self.assertEqual(st, 200, r)
        st, k = self.admin.call("GET", "/api/push/key?endpoint=" + urllib.parse.quote("https://fcm.googleapis.com/fcm/send/abc"))
        self.assertTrue(k["subscribed"])
        # signature du jeton : vérifiée avec la clé publique (ES256)
        jwt = self.ctx.push._jwt("https://fcm.googleapis.com/fcm/send/abc")
        head, body, sig = jwt.split(".")
        self.assertEqual(len(push_mod.base64.urlsafe_b64decode(sig + "==")), 64)
        claims = json.loads(push_mod.base64.urlsafe_b64decode(body + "=="))
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")
        # le message attend l'appareil réveillé
        sent = []
        self.ctx.push._send_one = lambda sub: sent.append(sub["endpoint"]) or True
        st, r = self.admin.call("POST", "/api/push/test")
        self.assertEqual((st, r["sent"]), (200, 1))
        st, p = self.admin.call("GET", "/api/push/pending")
        self.assertEqual(p["messages"][-1]["tag"], "test")
        for _ in range(50):
            if sent:
                break
            time.sleep(0.05)
        self.assertEqual(sent, ["https://fcm.googleapis.com/fcm/send/abc"])
        self.admin.call("POST", "/api/push/unsubscribe", {"endpoint": "https://fcm.googleapis.com/fcm/send/abc"})
        self.assertEqual(self.admin.call("POST", "/api/push/test")[0], 409)

    def test_12_local_only(self):
        for ip, ok in (("192.168.1.20", True), ("10.0.0.5", True), ("127.0.0.1", True), ("::ffff:192.168.0.3", True),
                       ("fe80::1", True), ("8.8.8.8", False), ("2a01:e0a::1", False), ("x", False)):
            self.assertEqual(dlna_mod.is_local(ip), ok, ip)


if __name__ == "__main__":
    unittest.main()
