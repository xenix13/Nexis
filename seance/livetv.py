"""TV en direct (IPTV) : listes de chaînes M3U, guide des programmes XMLTV, lecture dans le navigateur.

- Un administrateur ajoute une ou plusieurs sources : une liste M3U (adresse http(s) ou fichier sur le
  serveur) et, si besoin, un guide XMLTV (souvent indiqué dans l'en-tête de la liste, « url-tvg »).
- Toutes les personnes du foyer voient les chaînes (sauf les groupes masqués par l'administrateur).
- Le navigateur ne sait pas lire un flux MPEG-TS : Nexis le reçoit avec ffmpeg et le redécoupe en HLS
  (vidéo recopiée si elle est en H.264, son converti en AAC stéréo). Une chaîne regardée par plusieurs
  personnes n'est reçue qu'une fois, et ffmpeg s'arrête quand plus personne ne la regarde.
"""
import gzip
import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from . import media
from .names import fold

log = logging.getLogger("seance.live")

DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_M3U = 64 * 1024 * 1024
MAX_EPG = 512 * 1024 * 1024
MAX_LOGO = 2 * 1024 * 1024
EPG_PAST = 3 * 3600        # programmes déjà terminés gardés (pour l'affichage du guide)
EPG_AHEAD = 4 * 86400
IDLE_STOP = 45             # s sans requête d'un spectateur : la chaîne est coupée
CHECK_TIMEOUT = 8          # s pour qu'une chaîne réponde lors de la vérification
CHECK_GAP = 0.2            # s entre deux vérifications : pas de rafale chez le fournisseur
DEAD_AFTER = 2             # vérifications ratées d'affilée avant de masquer une chaîne
ATTR = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


class LiveError(Exception):
    pass


class LiveGone(LiveError):
    """La session a été arrêtée (plus personne ne regarde) pendant qu'on l'attendait."""


# ------------------------------------------------------------------ adresses autorisées
# Le contenu d'une liste M3U vient du fournisseur IPTV : ses adresses ne doivent pas permettre de faire lire au
# serveur ses propres fichiers ni d'utiliser les protocoles « internes » de ffmpeg (concat:, subfile:, data:…).
STREAM_SCHEMES = {"http", "https", "rtsp", "rtsps", "rtmp", "rtmps", "rtp", "udp"}
PROTOCOLS = {  # protocoles ffmpeg nécessaires pour chaque type d'adresse (-protocol_whitelist)
    "http": "http,https,tcp,tls,crypto", "https": "http,https,tcp,tls,crypto",
    "rtsp": "rtsp,rtsps,rtp,srtp,udp,tcp,tls", "rtsps": "rtsp,rtsps,rtp,srtp,udp,tcp,tls",
    "rtmp": "rtmp,rtmps,rtmpt,rtmpts,tcp,tls", "rtmps": "rtmp,rtmps,rtmpt,rtmpts,tcp,tls",
    "rtp": "rtp,udp", "udp": "udp", "file": "file",
}


def scheme_of(url):
    if url.startswith("/"):
        return "file"
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://", url)
    return m.group(1).lower() if m else None


def stream_url_ok(url, allow_local=False):
    """Adresse de chaîne acceptable : flux réseau, ou fichier local si la liste elle-même est un fichier
    du serveur (choisi par l'administrateur)."""
    sc = scheme_of(url or "")
    if sc == "file":
        return allow_local and ".." not in url.split("/")
    return sc in STREAM_SCHEMES and not re.search(r"[\s\x00-\x1f]", url)


def _gunzip(data, limit):
    """Décompression bornée (une archive minuscule peut se décompresser en plusieurs Go)."""
    try:
        out = gzip.GzipFile(fileobj=io.BytesIO(data)).read(limit + 1)
    except (OSError, EOFError) as e:
        raise LiveError(f"Archive illisible : {e}")
    if len(out) > limit:
        raise LiveError("Fichier trop volumineux une fois décompressé")
    return out


# ------------------------------------------------------------------ lecture des sources
def _open(url, ua=None, limit=MAX_M3U, timeout=60, allow_local=True):
    """Contenu d'une adresse http(s) ou (si permis) d'un fichier du serveur, décompressé si .gz."""
    if url.startswith("/"):
        if not allow_local:
            raise LiveError("Adresse refusée : seules les adresses http(s) sont acceptées ici")
        if not os.path.isfile(url):
            raise LiveError(f"Fichier introuvable sur le serveur : {url}")
        with open(url, "rb") as f:
            data = f.read(limit + 1)
    elif re.match(r"^https?://", url, re.I):
        req = urllib.request.Request(url, headers={"User-Agent": ua or DEFAULT_UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(limit + 1)
                if r.headers.get("Content-Encoding") == "gzip":
                    data = _gunzip(data, limit)
        except urllib.error.HTTPError as e:
            raise LiveError(f"{url} : erreur HTTP {e.code}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise LiveError(f"{url} : {getattr(e, 'reason', e)}")
    else:
        raise LiveError("Adresse invalide : http(s)://… ou chemin absolu d'un fichier du serveur")
    if len(data) > limit:
        raise LiveError("Fichier trop volumineux")
    if data[:2] == b"\x1f\x8b":
        data = _gunzip(data, limit * 4)
    return data


def check_stream(ch, timeout=CHECK_TIMEOUT):
    """La chaîne répond-elle ? True / False, ou None quand on ne sait pas vérifier (rtsp, udp…).

    On lit seulement le début de la réponse (une playlist HLS, ou quelques Ko d'un flux MPEG-TS)."""
    url = ch["url"]
    if not re.match(r"^https?://", url or "", re.I) or not stream_url_ok(url):
        return None
    headers = {"User-Agent": ch["user_agent"] or DEFAULT_UA}
    if ch["referrer"]:
        headers["Referer"] = ch["referrer"]
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            return 200 <= r.status < 300 and len(r.read(4096)) > 0
    except (urllib.error.URLError, OSError, ValueError):
        return False


def parse_m3u(text):
    """-> (en-tête {url-tvg…}, [chaînes]) ; chaque chaîne : name, url, group, logo, tvg_id, tvg_name, number…"""
    header, out, cur, opts = {}, [], None, {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if line.startswith("#EXTM3U"):
            header = {k.lower(): v for k, v in ATTR.findall(line)}
            continue
        if line.startswith("#EXTINF"):
            attrs = {k.lower(): v for k, v in ATTR.findall(line)}
            name = line.rsplit(",", 1)[1].strip() if "," in line else ""
            cur = {"name": name or attrs.get("tvg-name") or "Chaîne", "group": attrs.get("group-title", "").strip(),
                   "logo": attrs.get("tvg-logo") or None, "tvg_id": attrs.get("tvg-id") or None,
                   "tvg_name": attrs.get("tvg-name") or None, "number": None,
                   "user_agent": attrs.get("user-agent") or attrs.get("http-user-agent"), "referrer": None}
            chno = attrs.get("tvg-chno") or attrs.get("channel-number")
            if chno and chno.isdigit():
                cur["number"] = int(chno)
            opts = {}
            continue
        if line.startswith("#EXTGRP:") and cur is not None and not cur["group"]:
            cur["group"] = line[8:].strip()
            continue
        if line.startswith("#EXTVLCOPT:"):
            k, _, v = line[11:].partition("=")
            opts[k.strip().lower()] = v.strip()
            continue
        if line.startswith("#"):
            continue
        if cur is None:
            cur = {"name": line.rsplit("/", 1)[-1] or "Chaîne", "group": "", "logo": None, "tvg_id": None,
                   "tvg_name": None, "number": None, "user_agent": None, "referrer": None}
        cur["url"] = line
        cur["user_agent"] = cur["user_agent"] or opts.get("http-user-agent")
        cur["referrer"] = opts.get("http-referrer") or opts.get("http-referer")
        out.append(cur)
        cur, opts = None, {}
    return header, out


def xmltv_time(s):
    """« 20261023203000 +0200 » -> timestamp (UTC si aucun fuseau n'est indiqué)."""
    s = (s or "").strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?\s*([+-]\d{4})?", s)
    if not m:
        return None
    y, mo, d, hh, mi = (int(m.group(i)) for i in range(1, 6))
    ss = int(m.group(6) or 0)
    import calendar
    t = calendar.timegm((y, mo, d, hh, mi, ss, 0, 0, 0))
    tz = m.group(7)
    if tz:
        sign = 1 if tz[0] == "+" else -1
        t -= sign * (int(tz[1:3]) * 3600 + int(tz[3:5]) * 60)
    return t


def parse_xmltv(data, keys_by_id, keys_by_name, now=None):
    """Programmes des chaînes connues -> liste de (clé, début, fin, titre, sous-titre, description, catégorie)."""
    now = now or time.time()
    lo, hi = now - EPG_PAST, now + EPG_AHEAD
    id_map = {}
    out = []
    try:
        for ev, el in ET.iterparse(io.BytesIO(data), events=("end",)):
            tag = el.tag
            if tag == "channel":
                cid = el.get("id") or ""
                key = keys_by_id.get(cid)
                if not key:
                    for dn in el.findall("display-name"):
                        key = keys_by_name.get(fold(dn.text or ""))
                        if key:
                            break
                if not key:
                    key = keys_by_name.get(fold(cid))
                if key:
                    id_map[cid] = key
                el.clear()
            elif tag == "programme":
                cid = el.get("channel") or ""
                key = id_map.get(cid) or keys_by_id.get(cid) or keys_by_name.get(fold(cid))
                if key:
                    start, stop = xmltv_time(el.get("start")), xmltv_time(el.get("stop"))
                    if start and stop and stop > lo and start < hi and stop > start:
                        title = (el.findtext("title") or "").strip()
                        if title:
                            out.append((key, start, stop, title[:200], (el.findtext("sub-title") or "").strip()[:200] or None,
                                        (el.findtext("desc") or "").strip()[:1500] or None,
                                        (el.findtext("category") or "").strip()[:80] or None))
                el.clear()
    except ET.ParseError as e:
        if not out:
            raise LiveError(f"Guide XMLTV illisible : {e}")
    return out


def epg_key(ch):
    return ch.get("tvg_id") or fold(ch.get("tvg_name") or ch["name"])


# ------------------------------------------------------------------ sessions de lecture
class Session:
    def __init__(self, sid, channel, folder, mode):
        self.sid = sid
        self.channel = channel
        self.folder = folder
        self.mode = mode
        self.proc = None
        self.started = time.time()
        self.last = time.time()
        self.viewers = {}       # user_id -> (nom, dernière requête)
        self.err = []

    def alive(self):
        if self.proc is None:  # en cours de démarrage (analyse du flux)
            return time.time() - self.started < 30
        return self.proc.poll() is None

    def error(self):
        tail = [l for l in self.err if l.strip()][-3:]
        return " ".join(tail)[-300:] or "le flux s'est arrêté"


class LiveTV:
    def __init__(self, ctx):
        self.ctx = ctx
        self.base = os.path.join(ctx.data_dir, "live")
        self.logos = os.path.join(ctx.data_dir, "cache", "live-logos")
        shutil.rmtree(self.base, ignore_errors=True)
        os.makedirs(self.base, exist_ok=True)
        os.makedirs(self.logos, exist_ok=True)
        self.lock = threading.Lock()
        self.sessions = {}       # sid -> Session
        self.by_channel = {}     # channel id -> sid
        self.refreshing = threading.Lock()

    # ------------------------------------------------------------ réglages
    def settings(self):
        db = self.ctx.db
        return {"max_sessions": self.ctx.setting_int("live_max_sessions", 2),
                "video": db.setting("live_video", "auto"),
                "hide_restricted": db.setting("live_hide_restricted", "0") == "1"}

    # ------------------------------------------------------------ actualisation
    def refresh_source(self, src, progress=None):
        db = self.ctx.db
        try:
            raw = _open(src["m3u_url"], src["user_agent"])
            header, channels = parse_m3u(raw.decode("utf-8", "replace"))
            local_list = src["m3u_url"].startswith("/")
            channels = [c for c in channels if stream_url_ok(c["url"], local_list)]
            if not channels:
                raise LiveError("Aucune chaîne utilisable dans la liste")
            now = int(time.time())
            seen = {}
            with db.tx():
                existing = {r["uid"]: r["id"] for r in db.all("SELECT id, uid FROM live_channels WHERE source_id=?",
                                                               (src["id"],))}
                keep = set()
                for pos, ch in enumerate(channels):
                    base_uid = (ch["tvg_id"] or fold(ch["name"])) + "|" + fold(ch["group"])
                    n = seen.get(base_uid, 0)
                    seen[base_uid] = n + 1
                    uid = base_uid if not n else f"{base_uid}#{n + 1}"
                    vals = (ch["name"][:120], ch["number"], ch["group"][:80], ch["logo"], ch["tvg_id"], epg_key(ch),
                            ch["url"], ch["user_agent"], ch["referrer"], pos, fold(ch["name"]))
                    if uid in existing:
                        db.run("UPDATE live_channels SET name=?, number=?, grp=?, logo=?, tvg_id=?, epg_key=?, url=?, "
                               "user_agent=?, referrer=?, position=?, search=? WHERE id=?", (*vals, existing[uid]))
                        keep.add(existing[uid])
                    else:
                        cid = db.run("INSERT INTO live_channels(source_id,uid,name,number,grp,logo,tvg_id,epg_key,url,"
                                     "user_agent,referrer,position,search) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                     (src["id"], uid, *vals)).lastrowid
                        keep.add(cid)
                gone = [i for i in existing.values() if i not in keep]
                for i in range(0, len(gone), 500):
                    chunk = gone[i:i + 500]
                    db.run(f"DELETE FROM live_channels WHERE id IN ({','.join('?' * len(chunk))})", chunk)
                db.run("UPDATE live_sources SET refreshed_at=?, error=NULL WHERE id=?", (now, src["id"]))
            # guide choisi par l'administrateur, sinon celui qu'indique la liste (http(s) seulement, sauf si
            # la liste est elle-même un fichier du serveur)
            epg_url = src["epg_url"] or (header.get("url-tvg") or header.get("x-tvg-url") or "").split(",")[0].strip()
            n_prog = None
            if epg_url:
                if progress:
                    progress(f"{src['name']} : guide des programmes")
                n_prog = self.refresh_epg(src, epg_url, allow_local=bool(src["epg_url"]) or local_list)
            return len(channels), n_prog
        except LiveError as e:
            db.run("UPDATE live_sources SET error=?, refreshed_at=? WHERE id=?", (str(e)[:300], int(time.time()), src["id"]))
            raise

    def refresh_epg(self, src, url, allow_local=True):
        db = self.ctx.db
        try:
            data = _open(url, src["user_agent"], MAX_EPG, timeout=180, allow_local=allow_local)
        except LiveError as e:
            db.run("UPDATE live_sources SET error=? WHERE id=?", (f"Guide : {e}"[:300], src["id"]))
            return None
        chans = db.all("SELECT tvg_id, name, epg_key FROM live_channels WHERE source_id=?", (src["id"],))
        by_id = {c["tvg_id"]: c["epg_key"] for c in chans if c["tvg_id"]}
        by_name = {}
        for c in chans:
            by_name.setdefault(fold(c["name"]), c["epg_key"])
            by_name.setdefault(c["epg_key"], c["epg_key"])
        progs = parse_xmltv(data, by_id, by_name)
        with db.tx():
            db.run("DELETE FROM live_programmes WHERE source_id=?", (src["id"],))
            for p in progs:
                db.run("INSERT OR REPLACE INTO live_programmes(source_id,epg_key,start,stop,title,subtitle,descr,category) "
                       "VALUES(?,?,?,?,?,?,?,?)", (src["id"], *p))
            db.run("UPDATE live_sources SET epg_at=? WHERE id=?", (int(time.time()), src["id"]))
        return len(progs)

    def refresh_all(self, progress=None):
        db = self.ctx.db
        if not self.refreshing.acquire(blocking=False):
            return "Actualisation déjà en cours"
        try:
            sources = db.all("SELECT * FROM live_sources WHERE enabled=1 ORDER BY position, id")
            if not sources:
                return "Aucune source"
            done, errors, total_ch, total_prog = 0, [], 0, 0
            for n, src in enumerate(sources):
                if progress:
                    progress(n, len(sources), src["name"])
                try:
                    ch, prog = self.refresh_source(src, lambda label: progress and progress(n, len(sources), label))
                    total_ch += ch
                    total_prog += prog or 0
                    done += 1
                except LiveError as e:
                    errors.append(f"{src['name']} : {e}")
                finally:
                    db.release()
            out = f"{total_ch} chaîne(s), {total_prog} programme(s)"
            if errors:
                out += " — " + " ; ".join(errors)
                if not done:
                    raise LiveError(out)
            return out
        finally:
            self.refreshing.release()

    # ------------------------------------------------------------ vérification des chaînes
    def record_check(self, cid, ok):
        """Résultat d'une vérification : une chaîne est masquée après DEAD_AFTER échecs d'affilée."""
        if ok is None:
            return
        db = self.ctx.db
        now = int(time.time())
        if ok:
            db.run("UPDATE live_channels SET ok=1, fails=0, checked_at=? WHERE id=?", (now, cid))
        else:
            db.run("UPDATE live_channels SET fails=fails+1, checked_at=?, "
                   "ok=CASE WHEN fails+1 >= ? THEN 0 ELSE ok END WHERE id=?", (now, DEAD_AFTER, cid))

    def check_channels(self, source_id=None, progress=None, cancelled=None):
        """Vérifie une à une les chaînes des sources actives (groupes masqués exclus). -> résumé."""
        db = self.ctx.db
        where, args = "", []
        if source_id is not None:
            where, args = " AND c.source_id=?", [source_id]
        chans = db.all("SELECT c.id, c.url, c.user_agent, c.referrer FROM live_channels c JOIN live_sources s "
                       "ON s.id=c.source_id WHERE s.enabled=1 AND NOT EXISTS (SELECT 1 FROM live_hidden_groups g "
                       "WHERE g.source_id=c.source_id AND g.grp=c.grp)" + where +
                       " ORDER BY c.checked_at IS NOT NULL, c.checked_at, c.id", args)
        db.release()
        dead = 0
        for n, ch in enumerate(chans):
            if cancelled and cancelled():
                break
            if progress:
                progress(n, len(chans))
            with self.lock:
                playing = ch["id"] in self.by_channel  # en cours de lecture : elle répond, et on ne la dérange pas
            ok = True if playing else check_stream(ch)
            self.record_check(ch["id"], ok)
            dead += ok is False
            db.release()
            if not playing:
                time.sleep(CHECK_GAP)
        hidden = db.scalar("SELECT COUNT(*) FROM live_channels WHERE ok=0" +
                           (" AND source_id=?" if source_id is not None else ""), args)
        return f"{len(chans)} chaîne(s) vérifiée(s), {hidden} hors service masquée(s)"

    # ------------------------------------------------------------ logos
    def logo(self, url):
        name = hashlib.sha1(url.encode()).hexdigest()
        path = os.path.join(self.logos, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
        data = _open(url, limit=MAX_LOGO, timeout=15, allow_local=False)  # adresse venue de la liste M3U
        if not image_type(data):
            raise LiveError("logo dans un format non pris en charge")
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
        return data

    # ------------------------------------------------------------ lecture
    def _probe(self, ch):
        cmd = [media.FFPROBE, "-v", "error"] + self._input_opts(ch) + [
            "-analyzeduration", "4000000", "-probesize", "4000000", "-show_streams", "-of", "json", ch["url"]]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=15)
            if p.returncode != 0:
                return None
            return json.loads(p.stdout or b"{}").get("streams") or []
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return None

    @staticmethod
    def _input_opts(ch):
        """Options d'entrée de ffmpeg : protocoles strictement limités à ceux de l'adresse, en-têtes HTTP."""
        opts = ["-protocol_whitelist", PROTOCOLS.get(scheme_of(ch["url"]), "file")]
        if not re.match(r"^https?://", ch["url"], re.I):
            return opts
        opts += ["-user_agent", ch["user_agent"] or DEFAULT_UA]
        if ch["referrer"]:
            opts += ["-headers", f"Referer: {ch['referrer']}\r\n"]
        return opts

    def _command(self, ch, folder, streams):
        cfg = self.settings()
        video = next((s for s in streams or [] if s.get("codec_type") == "video"), None)
        vcodec = (video or {}).get("codec_name")
        interlaced = (video or {}).get("field_order") not in (None, "progressive", "unknown")
        transcode = cfg["video"] == "transcode" or (cfg["video"] == "auto" and video is not None and vcodec != "h264")
        cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error"] + self._input_opts(ch)
        if not re.match(r"^https?://", ch["url"], re.I):
            cmd += ["-re"]  # fichier ou périphérique local : lu au rythme du direct
        if re.match(r"^https?://", ch["url"], re.I) and not ch["url"].split("?")[0].lower().endswith(".m3u8"):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        cmd += ["-fflags", "+genpts+discardcorrupt", "-analyzeduration", "4000000", "-i", ch["url"],
                "-map", "0:v:0?", "-map", "0:a:0?", "-sn", "-dn"]
        if transcode:
            vf = ["yadif"] if interlaced else []
            vf.append("scale=-2:'min(1080,ih)'")
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-maxrate", "6M", "-bufsize", "12M",
                    "-pix_fmt", "yuv420p", "-g", "50", "-keyint_min", "50", "-sc_threshold", "0", "-vf", ",".join(vf)]
        else:
            cmd += ["-c:v", "copy"]
        cmd += ["-c:a", "aac", "-b:a", "160k", "-ac", "2",
                "-f", "hls", "-hls_time", "4", "-hls_list_size", "8", "-hls_allow_cache", "0",
                "-hls_flags", "delete_segments+omit_endlist+independent_segments",
                "-hls_segment_filename", os.path.join(folder, "s%06d.ts"), os.path.join(folder, "index.m3u8")]
        return cmd, ("transcode" if transcode else "copy"), vcodec

    def start(self, ch, user):
        """Session de lecture de la chaîne (partagée si quelqu'un la regarde déjà)."""
        src = self.ctx.db.one("SELECT m3u_url FROM live_sources WHERE id=?", (ch["source_id"],)) or {}
        if not stream_url_ok(ch["url"], (src.get("m3u_url") or "").startswith("/")):
            raise LiveError("Adresse de chaîne refusée")
        with self.lock:
            sid = self.by_channel.get(ch["id"])
            s = self.sessions.get(sid) if sid else None
            if s and s.alive():
                s.viewers[user["id"]] = (user["display_name"], time.time())
                s.last = time.time()
                return s
            if s:
                self._stop(s)
            running = [x for x in self.sessions.values() if x.alive()]
            limit = self.settings()["max_sessions"]
            if len(running) >= limit:
                raise LiveError(f"Déjà {len(running)} chaîne(s) regardée(s) en même temps (maximum : {limit}, "
                                "réglable dans Administration → TV en direct)")
            sid = secrets.token_urlsafe(9)
            folder = os.path.join(self.base, sid)
            os.makedirs(folder, exist_ok=True)
            s = Session(sid, dict(ch), folder, "copy")
            s.viewers[user["id"]] = (user["display_name"], time.time())
            self.sessions[sid] = s
            self.by_channel[ch["id"]] = sid
        streams = self._probe(ch)
        cmd, s.mode, s.vcodec = self._command(ch, folder, streams)
        log.debug("TV : %s", " ".join(cmd))
        try:
            s.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                      preexec_fn=lambda: os.nice(5))
        except OSError as e:
            with self.lock:
                self._stop(s)
            raise LiveError(f"ffmpeg : {e}")

        def drain(proc=s.proc):
            for line in proc.stderr:
                s.err.append(line.decode("utf-8", "replace").strip())
                del s.err[:-20]
        threading.Thread(target=drain, daemon=True).start()
        return s

    def session(self, sid, user):
        with self.lock:
            s = self.sessions.get(sid)
            if s:
                s.last = time.time()
                s.viewers[user["id"]] = (user["display_name"], time.time())
            return s

    def leave(self, sid, user):
        with self.lock:
            s = self.sessions.get(sid)
            if s:
                s.viewers.pop(user["id"], None)
                if not s.viewers:
                    self._stop(s)

    def wait_playlist(self, s, timeout=30):
        path = os.path.join(s.folder, "index.m3u8")
        t0 = time.time()
        while time.time() - t0 < timeout:
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    data = f.read()
                if b"#EXTINF" in data:
                    return data
            if s.sid not in self.sessions:
                raise LiveGone("Session terminée")
            if not s.alive():
                raise LiveError(f"La chaîne ne répond pas : {s.error()}")
            time.sleep(0.3)
        raise LiveError("La chaîne met trop de temps à démarrer")

    def _stop(self, s):
        if s.proc and s.proc.poll() is None:
            s.proc.terminate()
            try:
                s.proc.wait(5)
            except subprocess.TimeoutExpired:
                s.proc.kill()
        if s.proc and s.proc.stderr:
            try:
                s.proc.stderr.close()
            except OSError:
                pass
        self.sessions.pop(s.sid, None)
        if self.by_channel.get(s.channel["id"]) == s.sid:
            self.by_channel.pop(s.channel["id"], None)
        shutil.rmtree(s.folder, ignore_errors=True)

    def reap(self):
        now = time.time()
        with self.lock:
            for s in list(self.sessions.values()):
                for uid, (_, t) in list(s.viewers.items()):
                    if now - t > IDLE_STOP:
                        s.viewers.pop(uid, None)
                if now - s.last > IDLE_STOP or not s.viewers or (not s.alive() and now - s.last > 10):
                    self._stop(s)

    def stop_all(self):
        with self.lock:
            for s in list(self.sessions.values()):
                self._stop(s)

    def overview(self):
        with self.lock:
            return [{"sid": s.sid, "channel": s.channel["name"], "mode": s.mode, "codec": getattr(s, "vcodec", None),
                     "since": int(s.started), "viewers": [v[0] for v in s.viewers.values()], "alive": s.alive(),
                     "error": None if s.alive() else s.error()} for s in self.sessions.values()]


def image_type(data):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    from .web import HTTPError, Response
    db = ctx.db
    live = ctx.live
    CID = r"(?P<cid>\d{1,10})"
    LSID = r"(?P<sid>[A-Za-z0-9_-]{8,20})"

    def allowed(user):
        return not (live.settings()["hide_restricted"] and user.get("max_age") is not None)

    def require_live(user):
        if not allowed(user):
            raise HTTPError(404, "Introuvable")

    def visible_channels(where="", args=()):
        return db.all("SELECT c.* FROM live_channels c JOIN live_sources s ON s.id=c.source_id WHERE s.enabled=1 "
                      "AND NOT EXISTS (SELECT 1 FROM live_hidden_groups g WHERE g.source_id=c.source_id AND g.grp=c.grp)"
                      " AND c.ok IS NOT 0"  # chaînes hors service (vérification) : masquées
                      f"{where} ORDER BY s.position, s.id, COALESCE(c.number, 100000), c.position", args)

    def channel_or_404(user, cid):
        require_live(user)
        rows = visible_channels(" AND c.id=?", (int(cid),))
        if not rows:
            raise HTTPError(404, "Chaîne introuvable")
        return rows[0]

    def programmes(chans, start, stop):
        keys = {}
        for c in chans:
            keys.setdefault((c["source_id"], c["epg_key"]), []).append(c["id"])
        out = {}
        if not keys:
            return out
        for r in db.all("SELECT source_id, epg_key, start, stop, title, subtitle, descr, category FROM live_programmes "
                        "WHERE stop > ? AND start < ? ORDER BY start", (start, stop)):
            for cid in keys.get((r["source_id"], r["epg_key"]), ()):
                out.setdefault(cid, []).append({"start": r["start"], "stop": r["stop"], "title": r["title"],
                                                "subtitle": r["subtitle"], "desc": r["descr"], "category": r["category"]})
        return out

    def channel_view(c, favs, progs=None):
        v = {"id": str(c["id"]), "name": c["name"], "number": c["number"], "group": c["grp"],
             "logo": f"/api/live/logo/{c['id']}" if c["logo"] else None, "favorite": c["id"] in favs}
        if progs is not None:
            v["programmes"] = progs.get(c["id"], [])
        return v

    def favorites(user):
        return {r["channel_id"] for r in db.all("SELECT channel_id FROM live_favorites WHERE user_id=?", (user["id"],))}

    x.live_available = lambda user: allowed(user) and bool(db.scalar(
        "SELECT 1 FROM live_channels c JOIN live_sources s ON s.id=c.source_id WHERE s.enabled=1 LIMIT 1"))

    @app.route("GET", "/api/live")
    def live_channels(req):
        require_live(req.user)
        now = int(time.time())
        chans = visible_channels()
        progs = programmes(chans, now, now + 6 * 3600)
        favs = favorites(req.user)
        groups = []
        for c in chans:
            if c["grp"] not in groups:
                groups.append(c["grp"])
        out = []
        for c in chans:
            v = channel_view(c, favs)
            p = [x_ for x_ in progs.get(c["id"], []) if x_["stop"] > now]
            v["now"] = p[0] if p and p[0]["start"] <= now else None
            v["next"] = (p[1] if v["now"] and len(p) > 1 else (p[0] if p and not v["now"] else None))
            out.append(v)
        return {"channels": out, "groups": groups, "now": now}

    @app.route("GET", "/api/live/guide")
    def live_guide(req):
        require_live(req.user)
        start = req.int_arg("start", int(time.time()) - 1800)
        hours = req.int_arg("hours", 4, 1, 24)
        chans = visible_channels()
        grp = req.arg("group")
        if grp == "__fav__":
            favs = favorites(req.user)
            chans = [c for c in chans if c["id"] in favs]
        elif grp is not None:
            chans = [c for c in chans if c["grp"] == grp]
        q = fold(req.arg("q") or "")
        if q:
            chans = [c for c in chans if q in c["search"]]
        first = req.int_arg("offset", 0, 0)
        size = req.int_arg("size", 80, 1, 300)
        page = chans[first:first + size]
        progs = programmes(page, start, start + hours * 3600)
        favs = favorites(req.user)
        return {"start": start, "stop": start + hours * 3600, "total": len(chans),
                "channels": [channel_view(c, favs, progs) for c in page]}

    @app.route("GET", f"/api/live/{CID}")
    def live_channel(req):
        c = channel_or_404(req.user, req.params["cid"])
        now = int(time.time())
        progs = programmes([c], now - 3600, now + 12 * 3600)
        return channel_view(c, favorites(req.user), progs)

    @app.route("POST", f"/api/live/{CID}/favorite")
    def live_favorite(req):
        c = channel_or_404(req.user, req.params["cid"])
        if req.json().get("on"):
            db.run("INSERT OR IGNORE INTO live_favorites(user_id,channel_id) VALUES(?,?)", (req.user["id"], c["id"]))
        else:
            db.run("DELETE FROM live_favorites WHERE user_id=? AND channel_id=?", (req.user["id"], c["id"]))
        return {"ok": True}

    @app.route("GET", f"/api/live/logo/{CID}")
    def live_logo(req):
        c = channel_or_404(req.user, req.params["cid"])
        if not c["logo"]:
            raise HTTPError(404, "Pas de logo")
        db.release()
        try:
            data = live.logo(c["logo"])
        except LiveError:
            raise HTTPError(404, "Logo indisponible")
        return Response(data, 200, image_type(data) or "application/octet-stream",
                        {"Cache-Control": "private, max-age=86400"})

    @app.route("POST", f"/api/live/{CID}/play")
    def live_play(req):
        c = channel_or_404(req.user, req.params["cid"])
        if not media.available():
            raise HTTPError(503, "ffmpeg n'est pas installé sur le serveur")
        db.release()
        try:
            s = live.start(c, req.user)
        except LiveError as e:
            raise HTTPError(503, str(e))
        return {"session": s.sid, "url": f"/api/live/s/{s.sid}/index.m3u8", "mode": s.mode,
                "channel": channel_view(c, favorites(req.user))}

    def own(req):
        require_live(req.user)
        s = live.session(req.params["sid"], req.user)
        if not s:
            raise HTTPError(404, "Session terminée")
        return s

    @app.route("GET", f"/api/live/s/{LSID}/index.m3u8")
    def live_playlist(req):
        s = own(req)
        db.release()
        try:
            data = live.wait_playlist(s)
        except LiveGone as e:
            raise HTTPError(404, str(e))
        except LiveError as e:
            live.record_check(s.channel["id"], False)  # la chaîne ne répond pas : comptée comme une vérification ratée
            raise HTTPError(502, str(e))
        return Response(data, 200, "application/vnd.apple.mpegurl", {"Cache-Control": "no-store"})

    @app.route("GET", f"/api/live/s/{LSID}/(?P<seg>s\\d{{6}}\\.ts)")
    def live_segment(req):
        s = own(req)
        path = os.path.join(s.folder, req.params["seg"])
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            raise HTTPError(404, "Segment expiré")
        return Response(data, 200, "video/mp2t", {"Cache-Control": "no-store"})

    @app.route("POST", f"/api/live/s/{LSID}/stop")
    def live_stop(req):
        live.leave(req.params["sid"], req.user)
        return {"ok": True}

    # ------------------------------------------------------------ administration
    def source_view(s):
        n = db.scalar("SELECT COUNT(*) FROM live_channels WHERE source_id=?", (s["id"],))
        hidden = db.scalar("SELECT COUNT(*) FROM live_channels c WHERE source_id=? AND EXISTS (SELECT 1 FROM "
                           "live_hidden_groups g WHERE g.source_id=c.source_id AND g.grp=c.grp)", (s["id"],))
        progs = db.scalar("SELECT COUNT(*) FROM live_programmes WHERE source_id=?", (s["id"],))
        dead = db.scalar("SELECT COUNT(*) FROM live_channels WHERE source_id=? AND ok=0", (s["id"],))
        return {"id": str(s["id"]), "name": s["name"], "m3u_url": s["m3u_url"], "epg_url": s["epg_url"] or "",
                "user_agent": s["user_agent"] or "", "enabled": bool(s["enabled"]), "refreshed_at": s["refreshed_at"],
                "epg_at": s["epg_at"], "error": s["error"], "channels": n, "hidden": hidden, "programmes": progs,
                "dead": dead}

    def validate_source(b):
        name = str(b.get("name") or "").strip()
        if not 1 <= len(name) <= 60:
            raise HTTPError(400, "Nom : 1 à 60 caractères")
        out = {"name": name}
        for k, required in (("m3u_url", True), ("epg_url", False)):
            v = str(b.get(k) or "").strip()
            if not v and required:
                raise HTTPError(400, "Adresse de la liste M3U requise")
            if v and not (re.match(r"^https?://\S+$", v, re.I) or (v.startswith("/") and ".." not in v)):
                raise HTTPError(400, "Adresse invalide : http(s)://… ou chemin d'un fichier du serveur")
            if len(v) > 2000:
                raise HTTPError(400, "Adresse trop longue")
            out[k] = v or None
        ua = str(b.get("user_agent") or "").strip()
        if len(ua) > 300 or "\n" in ua or "\r" in ua:
            raise HTTPError(400, "User-Agent invalide")
        out["user_agent"] = ua or None
        out["enabled"] = 1 if b.get("enabled", True) else 0
        return out

    def admin_overview():
        return {"sources": [source_view(s) for s in db.all("SELECT * FROM live_sources ORDER BY position, id")],
                "settings": live.settings(), "sessions": live.overview()}

    def check_async(sid):
        """Vérifie en fond les chaînes d'une source (après ajout ou actualisation par l'administrateur)."""
        def job():
            try:
                live.check_channels(sid)
            except Exception:
                log.exception("Vérification des chaînes de la source %s", sid)
            finally:
                db.release()
        threading.Thread(target=job, daemon=True).start()

    def refresh_async(sid):
        def job():
            try:
                src = db.one("SELECT * FROM live_sources WHERE id=?", (sid,))
                if src:
                    live.refresh_source(src)
                    db.release()
                    live.check_channels(sid)
            except LiveError as e:
                log.info("Source TV %s : %s", sid, e)
            except Exception:
                log.exception("Source TV %s", sid)
            finally:
                db.release()
        threading.Thread(target=job, daemon=True).start()

    @app.route("GET", "/api/admin/live", auth="admin")
    def admin_live(req):
        return admin_overview()

    @app.route("POST", "/api/admin/live/sources", auth="admin")
    def admin_live_add(req):
        v = validate_source(req.json())
        pos = (db.scalar("SELECT MAX(position) FROM live_sources") or 0) + 1
        sid = db.run("INSERT INTO live_sources(name,m3u_url,epg_url,user_agent,enabled,position,created_at) "
                     "VALUES(?,?,?,?,?,?,?)", (v["name"], v["m3u_url"], v["epg_url"], v["user_agent"], v["enabled"], pos,
                                               int(time.time()))).lastrowid
        src = db.one("SELECT * FROM live_sources WHERE id=?", (sid,))
        db.release()
        try:
            live.refresh_source(src)
        except LiveError as e:
            return {"ok": True, "warning": str(e), **admin_overview()}
        check_async(sid)
        return {"ok": True, **admin_overview()}

    @app.route("PUT", r"/api/admin/live/sources/(?P<id>\d{1,6})", auth="admin")
    def admin_live_update(req):
        sid = int(req.params["id"])
        if not db.one("SELECT id FROM live_sources WHERE id=?", (sid,)):
            raise HTTPError(404, "Source introuvable")
        v = validate_source(req.json())
        db.run("UPDATE live_sources SET name=?, m3u_url=?, epg_url=?, user_agent=?, enabled=? WHERE id=?",
               (v["name"], v["m3u_url"], v["epg_url"], v["user_agent"], v["enabled"], sid))
        refresh_async(sid)
        return admin_overview()

    @app.route("DELETE", r"/api/admin/live/sources/(?P<id>\d{1,6})", auth="admin")
    def admin_live_delete(req):
        db.run("DELETE FROM live_sources WHERE id=?", (int(req.params["id"]),))
        return admin_overview()

    @app.route("POST", r"/api/admin/live/sources/(?P<id>\d{1,6})/refresh", auth="admin")
    def admin_live_refresh(req):
        src = db.one("SELECT * FROM live_sources WHERE id=?", (int(req.params["id"]),))
        if not src:
            raise HTTPError(404, "Source introuvable")
        db.release()
        try:
            ch, prog = live.refresh_source(src)
        except LiveError as e:
            raise HTTPError(502, str(e))
        check_async(src["id"])
        return {"channels": ch, "programmes": prog, **admin_overview()}

    @app.route("GET", r"/api/admin/live/sources/(?P<id>\d{1,6})/groups", auth="admin")
    def admin_live_groups(req):
        sid = int(req.params["id"])
        hidden = {r["grp"] for r in db.all("SELECT grp FROM live_hidden_groups WHERE source_id=?", (sid,))}
        rows = db.all("SELECT grp, COUNT(*) AS n, MIN(position) AS p FROM live_channels WHERE source_id=? GROUP BY grp "
                      "ORDER BY p", (sid,))
        return [{"name": r["grp"], "count": r["n"], "hidden": r["grp"] in hidden} for r in rows]

    @app.route("PUT", r"/api/admin/live/sources/(?P<id>\d{1,6})/groups", auth="admin")
    def admin_live_groups_set(req):
        sid = int(req.params["id"])
        hidden = req.json().get("hidden")
        if not isinstance(hidden, list) or not all(isinstance(g, str) and len(g) <= 80 for g in hidden):
            raise HTTPError(400, "Groupes invalides")
        with db.tx():
            db.run("DELETE FROM live_hidden_groups WHERE source_id=?", (sid,))
            for g in set(hidden):
                db.run("INSERT INTO live_hidden_groups(source_id,grp) VALUES(?,?)", (sid, g))
        return admin_live_groups(req)

    @app.route("PUT", "/api/admin/live/settings", auth="admin")
    def admin_live_settings(req):
        b = req.json()
        if "max_sessions" in b:
            try:
                n = int(b["max_sessions"])
            except (TypeError, ValueError):
                raise HTTPError(400, "Nombre invalide")
            if not 1 <= n <= 20:
                raise HTTPError(400, "Chaînes simultanées : entre 1 et 20")
            db.set_setting("live_max_sessions", n)
        if "video" in b:
            if b["video"] not in ("auto", "copy", "transcode"):
                raise HTTPError(400, "Mode vidéo invalide")
            db.set_setting("live_video", b["video"])
        if "hide_restricted" in b:
            db.set_setting("live_hide_restricted", "1" if b["hide_restricted"] else "0")
        return admin_overview()

    @app.route("DELETE", f"/api/admin/live/sessions/{LSID}", auth="admin")
    def admin_live_kill(req):
        with live.lock:
            s = live.sessions.get(req.params["sid"])
            if s:
                live._stop(s)
        return {"ok": True}
