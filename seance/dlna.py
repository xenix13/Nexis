"""Serveur DLNA / UPnP : les téléviseurs, consoles et box du réseau local voient Noxa et lisent ses fichiers.

- Annonce (SSDP) : multidiffusion sur 239.255.255.250:1900, réponse aux recherches des appareils.
- Description et services (ContentDirectory, ConnectionManager) sur le port web de Noxa, sous /dlna/.
- Contenu : les bibliothèques choisies par l'administrateur (films, séries > saisons > épisodes, photos >
  albums), servies telles quelles (le téléviseur lit le fichier d'origine ; pas de conversion).

Le DLNA ne connaît pas de comptes : tout appareil du réseau local voit les bibliothèques partagées. Le service
est désactivé par défaut et refuse les adresses qui ne sont pas locales.
"""
import http.client
import ipaddress
import json
import logging
import os
import secrets
import socket
import struct
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from . import __version__, media, photos
from .i18n import tr

log = logging.getLogger("seance.dlna")

GROUP, SSDP_PORT = "239.255.255.250", 1900
NOTIFY_EVERY = 600
MAX_AGE = 1800
DEVICE = "urn:schemas-upnp-org:device:MediaServer:1"
CD = "urn:schemas-upnp-org:service:ContentDirectory:1"
CM = "urn:schemas-upnp-org:service:ConnectionManager:1"
VIDEO_FLAGS = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"
IMAGE_FLAGS = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=00f00000000000000000000000000000"
JPEG_LRG = "DLNA.ORG_PN=JPEG_LRG;DLNA.ORG_OP=01;DLNA.ORG_CI=1;DLNA.ORG_FLAGS=00f00000000000000000000000000000"
VIDEO_MIME = {".mkv": "video/x-matroska", ".mk3d": "video/x-matroska", ".mp4": "video/mp4", ".m4v": "video/mp4",
              ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".ts": "video/vnd.dlna.mpeg-tts",
              ".m2ts": "video/vnd.dlna.mpeg-tts", ".mts": "video/vnd.dlna.mpeg-tts", ".mpg": "video/mpeg",
              ".mpeg": "video/mpeg", ".wmv": "video/x-ms-wmv", ".webm": "video/webm", ".flv": "video/x-flv",
              ".ogv": "video/ogg", ".3gp": "video/3gpp"}
CHILD_TYPES = {"show": ("season",), "season": ("episode",), "album": ("album", "photo")}
MAX_RESULTS = 5000


def is_local(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_private or a.is_loopback or a.is_link_local


def local_ip(towards=GROUP):
    """Adresse de ce serveur vue depuis `towards` (aucun paquet envoyé)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((towards, SSDP_PORT))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def video_mime(path):
    return VIDEO_MIME.get(os.path.splitext(path)[1].lower(), "video/mpeg")


def hms(ms):
    s = max(0, int(ms or 0)) / 1000
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"


class DLNA:
    def __init__(self, ctx):
        self.ctx = ctx
        self.http_port = None
        self.sock = None
        self.thread = None
        self.stop_event = threading.Event()
        self.error = None
        self.lock = threading.Lock()

    # ------------------------------------------------------------ réglages
    def enabled(self):
        return self.ctx.db.setting("dlna_enabled", "0") == "1"

    def uuid(self):
        db = self.ctx.db
        v = db.setting("dlna_uuid")
        if not v:
            h = secrets.token_hex(16)
            v = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"
            db.set_setting("dlna_uuid", v)
        return v

    def name(self):
        db = self.ctx.db
        return db.setting("dlna_name") or f"Noxa ({db.setting('server_name') or (socket.gethostname() or 'serveur')[:40]})"

    def library_ids(self):
        """Bibliothèques partagées (celles de ce serveur ; toutes si l'administrateur n'a rien choisi)."""
        db = self.ctx.db
        local = [r["id"] for r in db.all("SELECT id FROM libraries WHERE peer_id IS NULL AND kind<>'audiobook' "
                                         "ORDER BY position, name")]
        try:
            chosen = json.loads(db.setting("dlna_libraries") or "null")
        except ValueError:
            chosen = None
        if not isinstance(chosen, list):
            return local
        chosen = {str(c) for c in chosen}
        return [i for i in local if str(i) in chosen]

    def update_id(self):
        return int(self.ctx.db.scalar("SELECT COALESCE(MAX(scanned_at), 1) FROM libraries") or 1) % 2147483647

    # ------------------------------------------------------------ annonce (SSDP)
    def start(self, http_port=None):
        if http_port:
            self.http_port = http_port
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            try:
                on = self.enabled()
            finally:
                self.ctx.db.release()
            if not on or not self.http_port:
                return
            self.stop_event.clear()
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    try:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    except OSError:
                        pass
                s.bind(("", SSDP_PORT))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton("0.0.0.0")))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                s.settimeout(1.0)
            except OSError as e:
                self.error = f"Annonce DLNA impossible (port UDP {SSDP_PORT}) : {e.strerror or e}"
                log.error("%s", self.error)
                return
            self.error = None
            self.sock = s
            self.thread = threading.Thread(target=self._run, daemon=True, name="dlna-ssdp")
            self.thread.start()
            log.info("DLNA : serveur annoncé sur le réseau local (port web %s)", self.http_port)

    def stop(self):
        with self.lock:
            sock, self.sock = self.sock, None
            self.stop_event.set()
        if sock:
            try:
                self._notify(sock, "ssdp:byebye")
            except OSError:
                pass
            sock.close()

    def apply(self):
        """Réglages changés : (ré)annonce ou arrêt."""
        self.stop()
        if self.thread:
            self.thread.join(3)
            self.thread = None
        self.start()

    def _types(self, uuid):
        return [("upnp:rootdevice", f"uuid:{uuid}::upnp:rootdevice"), (f"uuid:{uuid}", f"uuid:{uuid}"),
                (DEVICE, f"uuid:{uuid}::{DEVICE}"), (CD, f"uuid:{uuid}::{CD}"), (CM, f"uuid:{uuid}::{CM}")]

    def location(self, ip):
        return f"http://{ip}:{self.http_port}/dlna/description.xml"

    def _notify(self, sock, nts):
        uuid = self.uuid()
        loc = self.location(local_ip())
        for nt, usn in self._types(uuid):
            msg = (f"NOTIFY * HTTP/1.1\r\nHOST: {GROUP}:{SSDP_PORT}\r\nCACHE-CONTROL: max-age={MAX_AGE}\r\n"
                   f"LOCATION: {loc}\r\nNT: {nt}\r\nNTS: {nts}\r\nSERVER: Linux/1.0 UPnP/1.0 Noxa/{__version__}\r\n"
                   f"USN: {usn}\r\n\r\n")
            sock.sendto(msg.encode(), (GROUP, SSDP_PORT))

    def _answer(self, sock, data, addr):
        head = data.decode("utf-8", "replace").split("\r\n")
        if not head[0].upper().startswith("M-SEARCH") or not is_local(addr[0]):
            return
        hdr = {}
        for line in head[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                hdr[k.strip().upper()] = v.strip()
        if "ssdp:discover" not in hdr.get("MAN", ""):
            return
        st = hdr.get("ST", "")
        uuid = self.uuid()
        loc = self.location(local_ip(addr[0]))
        for nt, usn in self._types(uuid):
            if st not in ("ssdp:all", nt):
                continue
            msg = (f"HTTP/1.1 200 OK\r\nCACHE-CONTROL: max-age={MAX_AGE}\r\nDATE: "
                   f"{time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())}\r\nEXT:\r\nLOCATION: {loc}\r\n"
                   f"SERVER: Linux/1.0 UPnP/1.0 Noxa/{__version__}\r\nST: {nt}\r\nUSN: {usn}\r\nContent-Length: 0\r\n\r\n")
            sock.sendto(msg.encode(), addr)

    def _run(self):
        sock = self.sock
        last = 0
        try:
            while not self.stop_event.is_set() and sock is self.sock:
                if time.time() - last > NOTIFY_EVERY:
                    try:
                        self._notify(sock, "ssdp:alive")
                    except OSError as e:
                        log.debug("DLNA : annonce : %s", e)
                    last = time.time()
                try:
                    data, addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    self._answer(sock, data, addr)
                except OSError as e:
                    log.debug("DLNA : réponse à %s : %s", addr, e)
        finally:
            self.ctx.db.release()

    # ------------------------------------------------------------ contenu
    def exposed(self, item):
        return item and item["peer_id"] is None and item["library_id"] in self.library_ids()

    def children(self, oid):
        """-> (lignes des enfants, objet parent) pour un identifiant d'objet, ou None s'il n'existe pas."""
        db = self.ctx.db
        if oid == "0":
            ids = self.library_ids()
            if not ids:
                return []
            return [("lib", l) for l in db.all(
                f"SELECT * FROM libraries WHERE id IN ({','.join('?' * len(ids))}) ORDER BY position, name", ids)]
        if oid.startswith("L") and oid[1:].isdigit():
            lid = int(oid[1:])
            lib = db.one("SELECT * FROM libraries WHERE id=?", (lid,))
            if not lib or lid not in self.library_ids():
                return None
            if lib["kind"] == "photo":
                rows = db.all("SELECT * FROM items WHERE library_id=? AND parent_id IS NULL AND type IN ('album','photo') "
                              "ORDER BY type='photo', CASE WHEN type='photo' THEN released END, sort_title", (lid,))
            else:
                rows = db.all("SELECT * FROM items WHERE library_id=? AND type=? AND peer_id IS NULL ORDER BY sort_title",
                              (lid, "movie" if lib["kind"] == "movie" else "show"))
            return [("item", r) for r in rows]
        if oid.startswith("I") and oid[1:].isdigit():
            parent = db.one("SELECT * FROM items WHERE id=?", (int(oid[1:]),))
            if not self.exposed(parent) or parent["type"] not in CHILD_TYPES:
                return None
            kinds = CHILD_TYPES[parent["type"]]
            order = "ORDER BY type='photo', released, sort_title" if parent["type"] == "album" else "ORDER BY idx, sort_title"
            rows = db.all(f"SELECT * FROM items WHERE parent_id=? AND peer_id IS NULL AND type IN "
                          f"({','.join('?' * len(kinds))}) {order}", (parent["id"], *kinds))
            return [("item", r) for r in rows]
        return None

    def parent_of(self, kind, r):
        if kind == "lib":
            return "0"
        if r["parent_id"] and r["type"] in ("season", "episode", "album", "photo"):
            return f"I{r['parent_id']}"
        return f"L{r['library_id']}"

    def count(self, kind, r):
        db = self.ctx.db
        if kind == "lib":
            if r["kind"] == "photo":
                return db.scalar("SELECT COUNT(*) FROM items WHERE library_id=? AND parent_id IS NULL AND type IN ('album','photo')",
                                 (r["id"],))
            return db.scalar("SELECT COUNT(*) FROM items WHERE library_id=? AND type=? AND peer_id IS NULL",
                             (r["id"], "movie" if r["kind"] == "movie" else "show"))
        kinds = CHILD_TYPES.get(r["type"], ())
        if not kinds:
            return 0
        return db.scalar(f"SELECT COUNT(*) FROM items WHERE parent_id=? AND peer_id IS NULL AND type IN "
                         f"({','.join('?' * len(kinds))})", (r["id"], *kinds))

    def didl(self, entries, base):
        out = ['<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
               'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">']
        for kind, r in entries:
            out.append(self._object(kind, r, base))
        out.append("</DIDL-Lite>")
        return "".join(x for x in out if x)

    def _object(self, kind, r, base):
        pid = self.parent_of(kind, r)
        if kind == "lib":
            return (f'<container id="L{r["id"]}" parentID="0" restricted="1" childCount="{self.count(kind, r)}" searchable="0">'
                    f'<dc:title>{escape(r["name"])}</dc:title><upnp:class>object.container.storageFolder</upnp:class></container>')
        t = r["type"]
        oid = f"I{r['id']}"
        art = f'<upnp:albumArtURI dlna:profileID="JPEG_TN">{escape(base)}/dlna/thumb/{r["id"]}.jpg</upnp:albumArtURI>'
        if t in CHILD_TYPES:
            title = tr(r["title"]) if t == "season" else r["title"]
            cls = "object.container.album.photoAlbum" if t == "album" else "object.container.storageFolder"
            return (f'<container id="{oid}" parentID="{pid}" restricted="1" childCount="{self.count(kind, r)}" searchable="0">'
                    f'<dc:title>{escape(title)}</dc:title><upnp:class>{cls}</upnp:class>{art}</container>')
        key = r["key"] or ""
        if not key or media.is_strm(key):
            return ""
        if t == "photo" and photos.is_clip(key):  # vidéo d'un album photo : lue telle quelle
            info = json.loads(r["probe"] or "{}")
            url = f'{escape(base)}/dlna/media/{r["id"]}{escape(os.path.splitext(key)[1].lower())}'
            dur = f' duration="{hms(info["dur"])}"' if info.get("dur") else ""
            return (f'<item id="{oid}" parentID="{pid}" restricted="1"><dc:title>{escape(r["title"])}</dc:title>'
                    f'<upnp:class>object.item.videoItem</upnp:class><dc:date>{escape((r["released"] or "")[:19])}</dc:date>{art}'
                    f'<res protocolInfo="http-get:*:{photos.mime(key)}:{VIDEO_FLAGS}" size="{r["file_size"] or 0}"{dur}>{url}</res></item>')
        if t == "photo":
            info = json.loads(r["probe"] or "{}")
            res = f'{escape(base)}/dlna/photo/{r["id"]}.jpg'
            orig = f'{escape(base)}/dlna/media/{r["id"]}{escape(os.path.splitext(key)[1].lower())}'
            size = f' resolution="{info["w"]}x{info["h"]}"' if info.get("w") and info.get("h") else ""
            return (f'<item id="{oid}" parentID="{pid}" restricted="1"><dc:title>{escape(r["title"])}</dc:title>'
                    f'<upnp:class>object.item.imageItem.photo</upnp:class>'
                    f'<dc:date>{escape((r["released"] or "")[:19])}</dc:date>{art}'
                    f'<res protocolInfo="http-get:*:image/jpeg:{JPEG_LRG}">{res}</res>'
                    f'<res protocolInfo="http-get:*:{photos.mime(key)}:{IMAGE_FLAGS}" size="{r["file_size"] or 0}"{size}>{orig}</res>'
                    f'</item>')
        probe = json.loads(r["probe"] or "{}")
        video = probe.get("video") or {}
        title = r["title"]
        if t == "episode":
            title = f"{r['idx'] or 0:02d}. {title}"
        attrs = f' size="{r["file_size"] or 0}"'
        if r["duration_ms"]:
            attrs += f' duration="{hms(r["duration_ms"])}"'
        if video.get("width") and video.get("height"):
            attrs += f' resolution="{video["width"]}x{video["height"]}"'
        cls = "object.item.videoItem.movie" if t == "movie" else "object.item.videoItem"
        url = f'{escape(base)}/dlna/media/{r["id"]}{escape(os.path.splitext(key)[1].lower())}'
        extra = f'<dc:description>{escape((r["summary"] or "")[:500])}</dc:description>' if r["summary"] else ""
        if r["year"]:
            extra += f"<dc:date>{int(r['year'])}-01-01</dc:date>"
        return (f'<item id="{oid}" parentID="{pid}" restricted="1"><dc:title>{escape(title)}</dc:title>'
                f'<upnp:class>{cls}</upnp:class>{extra}{art}'
                f'<res protocolInfo="http-get:*:{video_mime(key)}:{VIDEO_FLAGS}"{attrs}>{url}</res></item>')

    def browse(self, oid, flag, start, count, base):
        db = self.ctx.db
        if flag == "BrowseMetadata":
            if oid == "0":
                ids = self.library_ids()
                didl = ('<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
                        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"><container id="0" parentID="-1" restricted="1" '
                        f'childCount="{len(ids)}" searchable="0"><dc:title>{escape(self.name())}</dc:title>'
                        '<upnp:class>object.container.storageFolder</upnp:class></container></DIDL-Lite>')
                return didl, 1, 1
            if oid.startswith("L") and oid[1:].isdigit():
                lib = db.one("SELECT * FROM libraries WHERE id=?", (int(oid[1:]),))
                if not lib or lib["id"] not in self.library_ids():
                    return None
                return self.didl([("lib", lib)], base), 1, 1
            if oid.startswith("I") and oid[1:].isdigit():
                r = db.one("SELECT * FROM items WHERE id=?", (int(oid[1:]),))
                if not self.exposed(r):
                    return None
                return self.didl([("item", r)], base), 1, 1
            return None
        kids = self.children(oid)
        if kids is None:
            return None
        page = kids[start:start + (count or MAX_RESULTS)][:MAX_RESULTS]
        return self.didl(page, base), len(page), len(kids)


# ------------------------------------------------------------------ descriptions
def description(dlna, base):
    return f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:dlna="urn:schemas-dlna-org:device-1-0">
<specVersion><major>1</major><minor>0</minor></specVersion>
<device>
<deviceType>{DEVICE}</deviceType>
<friendlyName>{escape(dlna.name())}</friendlyName>
<manufacturer>Noxa</manufacturer>
<manufacturerURL>https://github.com/xenix13/Noxa</manufacturerURL>
<modelName>Noxa</modelName>
<modelDescription>Serveur multimédia Noxa</modelDescription>
<modelNumber>{__version__}</modelNumber>
<UDN>uuid:{dlna.uuid()}</UDN>
<dlna:X_DLNADOC>DMS-1.50</dlna:X_DLNADOC>
<presentationURL>{escape(base)}/</presentationURL>
<iconList><icon><mimetype>image/png</mimetype><width>192</width><height>192</height><depth>24</depth><url>/icons/icon-192.png</url></icon></iconList>
<serviceList>
<service><serviceType>{CD}</serviceType><serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
<SCPDURL>/dlna/cd.xml</SCPDURL><controlURL>/dlna/control/cd</controlURL><eventSubURL>/dlna/event/cd</eventSubURL></service>
<service><serviceType>{CM}</serviceType><serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
<SCPDURL>/dlna/cm.xml</SCPDURL><controlURL>/dlna/control/cm</controlURL><eventSubURL>/dlna/event/cm</eventSubURL></service>
</serviceList>
</device>
</root>"""


def _scpd(actions, variables):
    acts = "".join(
        f"<action><name>{name}</name><argumentList>" + "".join(
            f"<argument><name>{a}</name><direction>{d}</direction><relatedStateVariable>{v}</relatedStateVariable></argument>"
            for a, d, v in args) + "</argumentList></action>" for name, args in actions)
    vs = "".join(f'<stateVariable sendEvents="{"yes" if ev else "no"}"><name>{n}</name><dataType>{t}</dataType>'
                 + ("".join(f"<allowedValueList>{''.join(f'<allowedValue>{x}</allowedValue>' for x in allowed)}</allowedValueList>")
                    if allowed else "") + "</stateVariable>" for n, t, ev, allowed in variables)
    return ('<?xml version="1.0" encoding="utf-8"?><scpd xmlns="urn:schemas-upnp-org:service-1-0">'
            f"<specVersion><major>1</major><minor>0</minor></specVersion><actionList>{acts}</actionList>"
            f"<serviceStateTable>{vs}</serviceStateTable></scpd>")


CD_SCPD = _scpd(
    [("Browse", [("ObjectID", "in", "A_ARG_TYPE_ObjectID"), ("BrowseFlag", "in", "A_ARG_TYPE_BrowseFlag"),
                 ("Filter", "in", "A_ARG_TYPE_Filter"), ("StartingIndex", "in", "A_ARG_TYPE_Index"),
                 ("RequestedCount", "in", "A_ARG_TYPE_Count"), ("SortCriteria", "in", "A_ARG_TYPE_SortCriteria"),
                 ("Result", "out", "A_ARG_TYPE_Result"), ("NumberReturned", "out", "A_ARG_TYPE_Count"),
                 ("TotalMatches", "out", "A_ARG_TYPE_Count"), ("UpdateID", "out", "A_ARG_TYPE_UpdateID")]),
     ("GetSearchCapabilities", [("SearchCaps", "out", "SearchCapabilities")]),
     ("GetSortCapabilities", [("SortCaps", "out", "SortCapabilities")]),
     ("GetSystemUpdateID", [("Id", "out", "SystemUpdateID")])],
    [("A_ARG_TYPE_ObjectID", "string", False, None), ("A_ARG_TYPE_BrowseFlag", "string", False, ["BrowseMetadata", "BrowseDirectChildren"]),
     ("A_ARG_TYPE_Filter", "string", False, None), ("A_ARG_TYPE_Index", "ui4", False, None), ("A_ARG_TYPE_Count", "ui4", False, None),
     ("A_ARG_TYPE_SortCriteria", "string", False, None), ("A_ARG_TYPE_Result", "string", False, None),
     ("A_ARG_TYPE_UpdateID", "ui4", False, None), ("SearchCapabilities", "string", False, None),
     ("SortCapabilities", "string", False, None), ("SystemUpdateID", "ui4", True, None)])

CM_SCPD = _scpd(
    [("GetProtocolInfo", [("Source", "out", "SourceProtocolInfo"), ("Sink", "out", "SinkProtocolInfo")]),
     ("GetCurrentConnectionIDs", [("ConnectionIDs", "out", "CurrentConnectionIDs")]),
     ("GetCurrentConnectionInfo", [("ConnectionID", "in", "A_ARG_TYPE_ConnectionID"), ("RcsID", "out", "A_ARG_TYPE_RcsID"),
                                   ("AVTransportID", "out", "A_ARG_TYPE_AVTransportID"), ("ProtocolInfo", "out", "A_ARG_TYPE_ProtocolInfo"),
                                   ("PeerConnectionManager", "out", "A_ARG_TYPE_ConnectionManager"),
                                   ("PeerConnectionID", "out", "A_ARG_TYPE_ConnectionID"), ("Direction", "out", "A_ARG_TYPE_Direction"),
                                   ("Status", "out", "A_ARG_TYPE_ConnectionStatus")])],
    [("SourceProtocolInfo", "string", True, None), ("SinkProtocolInfo", "string", True, None),
     ("CurrentConnectionIDs", "string", True, None), ("A_ARG_TYPE_ConnectionID", "i4", False, None),
     ("A_ARG_TYPE_RcsID", "i4", False, None), ("A_ARG_TYPE_AVTransportID", "i4", False, None),
     ("A_ARG_TYPE_ProtocolInfo", "string", False, None), ("A_ARG_TYPE_ConnectionManager", "string", False, None),
     ("A_ARG_TYPE_Direction", "string", False, ["Input", "Output"]),
     ("A_ARG_TYPE_ConnectionStatus", "string", False, ["OK", "ContentFormatMismatch", "InsufficientBandwidth", "UnreliableChannel", "Unknown"])])


def soap(service, action, values):
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in values)
    return ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action}Response xmlns:u="{service}">{body}</u:{action}Response></s:Body></s:Envelope>')


def fault(code, text):
    return ('<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><s:Fault><faultcode>s:Client</faultcode>'
            '<faultstring>UPnPError</faultstring><detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
            f'<errorCode>{code}</errorCode><errorDescription>{escape(text)}</errorDescription></UPnPError></detail>'
            '</s:Fault></s:Body></s:Envelope>')


def parse_action(raw):
    """Corps SOAP -> (action, {argument: valeur})."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None, {}
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "Body":
            for action in el:
                return action.tag.rsplit("}", 1)[-1], {a.tag.rsplit("}", 1)[-1]: (a.text or "") for a in action}
    return None, {}


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    from .web import HTTPError, Response
    db = ctx.db
    dlna = ctx.dlna
    XML = 'text/xml; charset="utf-8"'

    def base(req):
        host = req.headers.get("Host") or f"{local_ip(req.client_ip)}:{dlna.http_port}"
        if not all(c.isalnum() or c in ".-:[]" for c in host):
            host = f"{local_ip(req.client_ip)}:{dlna.http_port}"
        return f"http://{host}"

    def xml(body, status=200):
        return Response(body.encode("utf-8"), status, XML, {"Cache-Control": "no-store", "EXT": ""})

    @app.route("GET", "/dlna/description.xml", auth="dlna")
    def dlna_description(req):
        return xml(description(dlna, base(req)))

    @app.route("GET", "/dlna/cd.xml", auth="dlna")
    def dlna_cd(req):
        return xml(CD_SCPD)

    @app.route("GET", "/dlna/cm.xml", auth="dlna")
    def dlna_cm(req):
        return xml(CM_SCPD)

    @app.route("POST", "/dlna/control/cd", auth="dlna")
    def dlna_control_cd(req):
        action, args = parse_action(req.body[:65536])
        if action == "Browse":
            def num(k):
                v = (args.get(k) or "0").strip()
                return int(v) if v.isdigit() else 0
            out = dlna.browse((args.get("ObjectID") or "0").strip(), (args.get("BrowseFlag") or "").strip(),
                              num("StartingIndex"), num("RequestedCount"), base(req))
            if out is None:
                return xml(fault(701, "No such object"), 500)
            didl, n, total = out
            return xml(soap(CD, "Browse", [("Result", didl), ("NumberReturned", n), ("TotalMatches", total),
                                           ("UpdateID", dlna.update_id())]))
        if action == "GetSystemUpdateID":
            return xml(soap(CD, action, [("Id", dlna.update_id())]))
        if action == "GetSearchCapabilities":
            return xml(soap(CD, action, [("SearchCaps", "")]))
        if action == "GetSortCapabilities":
            return xml(soap(CD, action, [("SortCaps", "")]))
        return xml(fault(401, "Invalid Action"), 500)

    @app.route("POST", "/dlna/control/cm", auth="dlna")
    def dlna_control_cm(req):
        action, args = parse_action(req.body[:65536])
        if action == "GetProtocolInfo":
            src = ",".join([f"http-get:*:{m}:*" for m in sorted(set(VIDEO_MIME.values()))]
                           + [f"http-get:*:{m}:*" for m in sorted(set(photos.MIME.values()))])
            return xml(soap(CM, action, [("Source", src), ("Sink", "")]))
        if action == "GetCurrentConnectionIDs":
            return xml(soap(CM, action, [("ConnectionIDs", "0")]))
        if action == "GetCurrentConnectionInfo":
            return xml(soap(CM, action, [("RcsID", -1), ("AVTransportID", -1), ("ProtocolInfo", ""),
                                         ("PeerConnectionManager", ""), ("PeerConnectionID", -1),
                                         ("Direction", "Output"), ("Status", "OK")]))
        return xml(fault(401, "Invalid Action"), 500)

    @app.route("SUBSCRIBE", r"/dlna/event/(?P<svc>cd|cm)", auth="dlna")
    def dlna_subscribe(req):
        """Abonnement aux événements (certains téléviseurs l'exigent) : accepté, avec l'état initial."""
        sid = req.headers.get("SID") or f"uuid:{secrets.token_hex(16)}"
        callback = (req.headers.get("CALLBACK") or "").strip("<> ")
        if callback and not req.headers.get("SID"):
            threading.Thread(target=initial_event, args=(callback, sid, req.client_ip, req.params["svc"]), daemon=True).start()
        return Response(b"", 200, "text/plain", {"SID": sid, "TIMEOUT": "Second-1800", "Server": f"Linux/1.0 UPnP/1.0 Noxa/{__version__}"})

    @app.route("UNSUBSCRIBE", r"/dlna/event/(?P<svc>cd|cm)", auth="dlna")
    def dlna_unsubscribe(req):
        return Response(b"", 200, "text/plain")

    def initial_event(callback, sid, client_ip, svc):
        u = urllib.parse.urlsplit(callback)
        if u.scheme != "http" or u.hostname != client_ip:  # on ne prévient que l'appareil qui s'abonne
            return
        props = ([("SystemUpdateID", dlna.update_id()), ("ContainerUpdateIDs", "")] if svc == "cd"
                 else [("SourceProtocolInfo", ""), ("SinkProtocolInfo", ""), ("CurrentConnectionIDs", "0")])
        body = ('<?xml version="1.0" encoding="utf-8"?><e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">'
                + "".join(f"<e:property><{k}>{escape(str(v))}</{k}></e:property>" for k, v in props) + "</e:propertyset>")
        try:
            c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=5)
            c.request("NOTIFY", u.path or "/", body=body.encode(), headers={
                "Content-Type": XML, "NT": "upnp:event", "NTS": "upnp:propchange", "SID": sid, "SEQ": "0"})
            c.getresponse().read()
            c.close()
        except (OSError, http.client.HTTPException) as e:
            log.debug("DLNA : événement initial vers %s : %s", callback, e)
        finally:
            db.release()

    def exposed_item(rk, types):
        r = db.one("SELECT * FROM items WHERE id=?", (int(rk),))
        if not r or r["type"] not in types or not dlna.exposed(r) or not r["key"] or not os.path.isfile(r["key"]):
            raise HTTPError(404, "Introuvable")
        return r

    def send_file(req, path, ctype, flags, mode):
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        rng = (req.headers.get("Range") or "").strip()
        if rng.startswith("bytes="):
            a, _, b = rng[6:].split(",")[0].partition("-")
            try:
                if a.strip():
                    start = int(a)
                    end = min(int(b), size - 1) if b.strip() else size - 1
                elif b.strip():
                    start = max(0, size - int(b))
            except ValueError:
                raise HTTPError(416, "Plage invalide")
            if start > end or start >= size:
                return Response(b"", 416, "text/plain", {"Content-Range": f"bytes */{size}"})
            status = 206
        length = end - start + 1
        db.release()
        f = open(path, "rb")
        f.seek(start)

        def gen():
            left = length
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk
        headers = {"Accept-Ranges": "bytes", "transferMode.dlna.org": mode, "contentFeatures.dlna.org": flags,
                   "Cache-Control": "no-store"}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(stream=gen(), status=status, content_type=ctype, length=length, headers=headers, on_close=f.close)

    @app.route("GET", r"/dlna/media/(?P<rk>\d{1,12})(?:\.[A-Za-z0-9]{1,5})?", auth="dlna")
    def dlna_media(req):
        r = exposed_item(req.params["rk"], ("movie", "episode", "photo"))
        if media.is_strm(r["key"]):
            raise HTTPError(404, "Introuvable")
        if r["type"] == "photo" and photos.is_clip(r["key"]):
            return send_file(req, r["key"], photos.mime(r["key"]), VIDEO_FLAGS, "Streaming")
        if r["type"] == "photo":
            return send_file(req, r["key"], photos.mime(r["key"]), IMAGE_FLAGS, "Interactive")
        return send_file(req, r["key"], video_mime(r["key"]), VIDEO_FLAGS, "Streaming")

    @app.route("GET", r"/dlna/photo/(?P<rk>\d{1,12})\.jpg", auth="dlna")
    def dlna_photo(req):
        """Photo en JPEG 1920 px, remise à l'endroit : ce que tous les téléviseurs savent afficher (HEIC compris)."""
        r = exposed_item(req.params["rk"], ("photo",))
        db.release()
        path = photos.render(ctx, r, 1920, 1920, crop=False)
        if not path:
            raise HTTPError(404, "Image illisible")
        return send_file(req, path, "image/jpeg", JPEG_LRG, "Interactive")

    @app.route("GET", r"/dlna/thumb/(?P<rk>\d{1,12})\.jpg", auth="dlna")
    def dlna_thumb(req):
        r = db.one("SELECT * FROM items WHERE id=?", (int(req.params["rk"]),))
        if not dlna.exposed(r):
            raise HTTPError(404, "Introuvable")
        square = r["type"] in ("photo", "album")
        path = ctx.images.get(r, "thumb", 320, 320 if square else 480)
        if not path:
            raise HTTPError(404, "Pas d'image")
        with open(path, "rb") as f:
            return Response(f.read(), 200, "image/jpeg", {"Cache-Control": "max-age=86400"})

    # ------------------------------------------------------------ administration
    def admin_view():
        libs = db.all("SELECT id, name, kind FROM libraries WHERE peer_id IS NULL ORDER BY position, name")
        shared = set(dlna.library_ids())
        running = bool(dlna.thread and dlna.thread.is_alive())
        return {"enabled": dlna.enabled(), "name": dlna.name(), "custom_name": db.setting("dlna_name") or "",
                "running": running, "error": tr(dlna.error) if dlna.error else None,
                "address": f"http://{local_ip()}:{dlna.http_port}/dlna/description.xml" if dlna.http_port else None,
                "libraries": [{"id": str(l["id"]), "name": l["name"], "kind": l["kind"], "shared": l["id"] in shared}
                              for l in libs]}

    @app.route("GET", "/api/admin/dlna", auth="admin")
    def admin_dlna(req):
        return admin_view()

    @app.route("PUT", "/api/admin/dlna", auth="admin")
    def admin_dlna_put(req):
        b = req.json()
        if "name" in b:
            name = str(b.get("name") or "").strip()
            if len(name) > 60:
                raise HTTPError(400, "Nom : 60 caractères au plus")
            db.set_setting("dlna_name", name or None)
        if "libraries" in b:
            libs = b["libraries"]
            if not isinstance(libs, list) or not all(str(i).isdigit() for i in libs):
                raise HTTPError(400, "Bibliothèques invalides")
            db.set_setting("dlna_libraries", json.dumps(sorted({str(i) for i in libs}, key=int)))
        if "enabled" in b:
            db.set_setting("dlna_enabled", "1" if b["enabled"] else "0")
        db.release()
        dlna.apply()
        return admin_view()
