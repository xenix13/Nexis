"""Podcasts : abonnements à des flux RSS, propres à chaque compte.

- Recherche dans l'annuaire d'Apple Podcasts (sans clé), ou adresse d'un flux RSS collée telle quelle.
- Les flux sont lus par le serveur (toutes les 2 heures, tâche « Actualiser les podcasts ») ; un nouvel épisode
  est signalé par notification push aux abonnés.
- L'audio et les images passent par Nexis : l'appareil ne contacte pas l'hébergeur du podcast (son adresse IP
  reste privée), et un épisode en HTTP s'écoute aussi sur un Nexis en HTTPS.
- Protection : les adresses données par les utilisateurs ne peuvent pas viser le réseau local du serveur (sauf
  pour un administrateur), y compris après une redirection.
"""
import email.utils
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import __version__ as VERSION

log = logging.getLogger("seance.podcasts")

SEARCH_API = os.environ.get("SEANCE_PODCAST_SEARCH", "https://itunes.apple.com/search")
MAX_FEED = 15 * 1024 * 1024
MAX_IMAGE = 3 * 1024 * 1024
KEEP_EPISODES = 500
USER_AGENT = f"Nexis/{VERSION} (podcasts)"
ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
TAGS = re.compile(r"<[^>]{0,2000}>")


class PodcastError(Exception):
    pass


# ------------------------------------------------------------------ accès réseau protégé
def public_host(host, allow_private=False):
    """L'hôte ne doit pas désigner le serveur ou son réseau local (attaque SSRF), sauf pour un administrateur."""
    if allow_private:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise PodcastError("Adresse introuvable")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
    return True


def check_url(url, allow_private=False):
    u = urllib.parse.urlparse(url or "")
    if u.scheme not in ("http", "https") or not u.hostname or len(url) > 2000:
        raise PodcastError("Adresse invalide (http:// ou https://)")
    if not public_host(u.hostname, allow_private):
        raise PodcastError("Adresse refusée : elle vise le réseau local du serveur")
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.allow_private)  # chaque étape de la redirection est vérifiée
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url, headers=None, allow_private=False, timeout=30):
    check_url(url, allow_private)
    opener = urllib.request.build_opener(_Redirects(allow_private))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    return opener.open(req, timeout=timeout)


def fetch(url, limit, allow_private=False, headers=None):
    try:
        with open_url(url, headers, allow_private) as r:
            data = r.read(limit + 1)
            if len(data) > limit:
                raise PodcastError("Réponse trop volumineuse")
            return r.status, dict(r.headers), data
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, dict(e.headers), b""
        raise PodcastError(f"Le serveur du podcast répond {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise PodcastError(f"Podcast injoignable : {getattr(e, 'reason', e)}")


# ------------------------------------------------------------------ flux RSS
def text(el, path):
    found = el.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def clean(s, limit=4000):
    return html.unescape(TAGS.sub(" ", s or "")).replace("\xa0", " ").strip()[:limit]


def duration_ms(v):
    v = (v or "").strip()
    if not v:
        return None
    try:
        if ":" in v:
            parts = [float(p) for p in v.split(":")]
            secs = 0
            for p in parts:
                secs = secs * 60 + p
            return int(secs * 1000)
        return int(float(v) * 1000)
    except ValueError:
        return None


def parse_feed(data):
    """RSS 2.0 (avec les balises iTunes) -> {title, author, image, description, link, episodes: [...]}."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise PodcastError("Ce n'est pas un flux RSS de podcast")
    ch = root.find("channel")
    if ch is None:
        raise PodcastError("Ce n'est pas un flux RSS de podcast")
    img = ch.find(f"{ITUNES}image")
    image = img.get("href") if img is not None else text(ch, "image/url")
    out = {"title": clean(text(ch, "title"), 300) or "Podcast", "author": clean(text(ch, f"{ITUNES}author"), 200),
           "image": image or "", "description": clean(text(ch, "description") or text(ch, f"{ITUNES}summary")),
           "link": text(ch, "link")[:500], "episodes": []}
    for it in ch.findall("item"):
        enc = it.find("enclosure")
        url = enc.get("url") if enc is not None else ""
        if not url or not re.match(r"^https?://", url):
            continue
        guid = text(it, "guid") or url
        try:
            published = int(email.utils.parsedate_to_datetime(text(it, "pubDate")).timestamp())
        except (TypeError, ValueError, IndexError):
            published = 0
        eimg = it.find(f"{ITUNES}image")
        out["episodes"].append({
            "guid": guid[:500], "title": clean(text(it, "title"), 300) or "Épisode", "published": published,
            "duration": duration_ms(text(it, f"{ITUNES}duration")), "url": url[:2000],
            "mime": (enc.get("type") or "audio/mpeg")[:60], "image": (eimg.get("href") if eimg is not None else "")[:2000],
            "description": clean(text(it, f"{ITUNES}summary") or text(it, "description"), 6000)})
    out["episodes"].sort(key=lambda e: -e["published"])
    return out


class Podcasts:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.Lock()
        self.search_images = {}  # images de résultats de recherche affichables (adresse -> instant)

    def add_or_update(self, url, allow_private=False, notify=False):
        """Lit le flux et enregistre le podcast et ses épisodes -> (identifiant, nouveaux épisodes)."""
        db = self.ctx.db
        old = db.one("SELECT * FROM podcasts WHERE url=?", (url,))
        headers = {}
        if old and old["etag"]:
            headers["If-None-Match"] = old["etag"]
        status, h, data = fetch(url, MAX_FEED, allow_private or bool(old and old["private"]), headers)
        now = int(time.time())
        if status == 304 and old:
            db.run("UPDATE podcasts SET checked_at=?, error=NULL WHERE id=?", (now, old["id"]))
            return old["id"], []
        feed = parse_feed(data)
        with db.tx():
            if old:
                pid = old["id"]
                db.run("UPDATE podcasts SET title=?, author=?, image=?, description=?, link=?, etag=?, checked_at=?, error=NULL "
                       "WHERE id=?", (feed["title"], feed["author"], feed["image"], feed["description"], feed["link"],
                                      h.get("ETag") or h.get("Etag"), now, pid))
            else:
                pid = db.run("INSERT INTO podcasts(url, title, author, image, description, link, etag, checked_at, created_at, "
                             "private) VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (url, feed["title"], feed["author"], feed["image"], feed["description"], feed["link"],
                              h.get("ETag") or h.get("Etag"), now, now, 1 if allow_private else 0)).lastrowid
            known = {r["guid"] for r in db.all("SELECT guid FROM podcast_episodes WHERE podcast_id=?", (pid,))}
            fresh = []
            for e in feed["episodes"][:KEEP_EPISODES]:
                if e["guid"] in known:
                    db.run("UPDATE podcast_episodes SET title=?, duration_ms=COALESCE(?, duration_ms), url=?, mime=?, image=?, "
                           "description=? WHERE podcast_id=? AND guid=?",
                           (e["title"], e["duration"], e["url"], e["mime"], e["image"], e["description"], pid, e["guid"]))
                    continue
                db.run("INSERT INTO podcast_episodes(podcast_id, guid, title, published, duration_ms, url, mime, image, description, "
                       "added_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (pid, e["guid"], e["title"], e["published"], e["duration"], e["url"], e["mime"], e["image"],
                        e["description"], now))
                fresh.append(e)
        if notify and old and fresh:
            self.announce(pid, feed["title"], fresh)
        return pid, fresh

    def announce(self, pid, title, fresh):
        from .i18n import tr
        db = self.ctx.db
        for u in db.all("SELECT u.id, u.language FROM podcast_subs s JOIN users u ON u.id=s.user_id WHERE s.podcast_id=? "
                        "AND u.disabled=0", (pid,)):
            body = fresh[0]["title"] if len(fresh) == 1 else tr("{n} nouveaux épisodes", u["language"]).format(n=len(fresh))
            self.ctx.push.notify(u["id"], title, body, f"#/podcasts/{pid}", tag=f"podcast-{pid}")

    def refresh_all(self, job=None):
        db = self.ctx.db
        rows = db.all("SELECT * FROM podcasts WHERE EXISTS (SELECT 1 FROM podcast_subs s WHERE s.podcast_id=podcasts.id)")
        db.release()
        new = errors = 0
        for n, p in enumerate(rows):
            if job:
                job.check()
                job.progress = n / max(1, len(rows))
                job.message = p["title"]
            try:
                _, fresh = self.add_or_update(p["url"], notify=True)
                new += len(fresh)
            except PodcastError as e:
                errors += 1
                db.run("UPDATE podcasts SET error=?, checked_at=? WHERE id=?", (str(e), int(time.time()), p["id"]))
            finally:
                db.release()
        # podcasts sans abonné : oubliés au bout d'un mois
        db.run("DELETE FROM podcasts WHERE NOT EXISTS (SELECT 1 FROM podcast_subs s WHERE s.podcast_id=podcasts.id) "
               "AND created_at<?", (int(time.time()) - 30 * 86400,))
        return new, errors, len(rows)

    def search(self, term):
        qs = urllib.parse.urlencode({"media": "podcast", "entity": "podcast", "limit": 25, "term": term[:100]})
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{SEARCH_API}?{qs}", headers={"User-Agent": USER_AGENT}),
                                        timeout=20) as r:
                data = json.loads(r.read(2 * 1024 * 1024) or b"{}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise PodcastError(f"Recherche impossible : {getattr(e, 'reason', e)}")
        out = []
        now = time.time()
        for it in data.get("results") or []:
            feed = it.get("feedUrl")
            if not feed:
                continue
            img = it.get("artworkUrl600") or it.get("artworkUrl100") or ""
            if img:
                self.search_images[img] = now
            out.append({"title": it.get("collectionName") or it.get("trackName") or "", "author": it.get("artistName") or "",
                        "feed": feed, "image": img, "genre": it.get("primaryGenreName") or "", "episodes": it.get("trackCount")})
        with self.lock:
            for k, t in list(self.search_images.items()):
                if now - t > 3600:
                    del self.search_images[k]
        return out

    def image(self, url, private=False):
        """Image d'un podcast ou d'un épisode, gardée en cache -> chemin du fichier."""
        cache = os.path.join(self.ctx.data_dir, "cache", "podcasts")
        os.makedirs(cache, exist_ok=True)
        path = os.path.join(cache, hashlib.sha1(url.encode()).hexdigest())
        if os.path.isfile(path) and time.time() - os.path.getmtime(path) < 30 * 86400:
            return path
        status, h, data = fetch(url, MAX_IMAGE, private)
        magic = data[:3] == b"\xff\xd8\xff" or data[:4] in (b"\x89PNG", b"RIFF", b"GIF8")
        if not magic:
            raise PodcastError("Ce n'est pas une image")
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
        return path


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    from .web import HTTPError, Response
    db = ctx.db
    pc = ctx.podcasts
    PID = r"(?P<pid>\d{1,10})"
    EID = r"(?P<eid>\d{1,12})"

    def img_url(u):
        return f"/api/podcasts/image?u={urllib.parse.quote(u, safe='')}" if u else None

    def podcast_card(r, uid):
        unplayed = db.scalar("SELECT COUNT(*) FROM podcast_episodes e WHERE e.podcast_id=? AND NOT EXISTS (SELECT 1 FROM "
                             "podcast_progress p WHERE p.user_id=? AND p.episode_id=e.id AND p.played=1)", (r["id"], uid))
        latest = db.scalar("SELECT MAX(published) FROM podcast_episodes WHERE podcast_id=?", (r["id"],))
        return {"id": str(r["id"]), "title": r["title"], "author": r["author"], "image": img_url(r["image"]),
                "description": r["description"], "unplayed": unplayed, "latest": latest, "error": r["error"], "url": r["url"]}

    def episode_card(e, prog=None, podcast=None):
        c = {"id": str(e["id"]), "podcast": str(e["podcast_id"]), "title": e["title"], "published": e["published"],
             "duration": e["duration_ms"], "image": img_url(e["image"]) or (img_url(podcast["image"]) if podcast else None),
             "description": e["description"], "audio": f"/api/podcasts/episodes/{e['id']}/audio",
             "position": prog["position_ms"] if prog else 0, "played": bool(prog and prog["played"])}
        if podcast:
            c["podcast_title"] = podcast["title"]
        return c

    def subscribed(uid, pid):
        r = db.one("SELECT p.* FROM podcasts p JOIN podcast_subs s ON s.podcast_id=p.id WHERE p.id=? AND s.user_id=?", (pid, uid))
        if not r:
            raise HTTPError(404, "Podcast introuvable")
        return r

    @app.route("GET", "/api/podcasts")
    def mine(req):
        uid = req.user["id"]
        rows = db.all("SELECT p.* FROM podcasts p JOIN podcast_subs s ON s.podcast_id=p.id WHERE s.user_id=? ORDER BY "
                      "(SELECT MAX(published) FROM podcast_episodes e WHERE e.podcast_id=p.id) DESC", (uid,))
        cards = [podcast_card(r, uid) for r in rows]
        # épisodes à reprendre, et les derniers sortis
        resume = db.all("SELECT e.*, pr.position_ms, pr.played, pr.updated_at AS listened FROM podcast_progress pr JOIN "
                        "podcast_episodes e ON e.id=pr.episode_id JOIN podcast_subs s ON s.podcast_id=e.podcast_id AND "
                        "s.user_id=pr.user_id WHERE pr.user_id=? AND pr.played=0 AND pr.position_ms>0 ORDER BY pr.updated_at DESC "
                        "LIMIT 10", (uid,))
        latest = db.all("SELECT e.* FROM podcast_episodes e JOIN podcast_subs s ON s.podcast_id=e.podcast_id WHERE s.user_id=? "
                        "AND NOT EXISTS (SELECT 1 FROM podcast_progress pr WHERE pr.user_id=? AND pr.episode_id=e.id AND pr.played=1) "
                        "ORDER BY e.published DESC LIMIT 20", (uid, uid))
        pods = {r["id"]: r for r in rows}
        return {"podcasts": cards,
                "resume": [episode_card(e, {"position_ms": e["position_ms"], "played": e["played"]}, pods.get(e["podcast_id"]))
                           for e in resume],
                "latest": [episode_card(e, None, pods.get(e["podcast_id"])) for e in latest]}

    @app.route("GET", "/api/podcasts/search")
    def search(req):
        q = (req.arg("q") or "").strip()
        if len(q) < 2:
            return {"results": []}
        db.release()
        try:
            results = pc.search(q)
        except PodcastError as e:
            raise HTTPError(502, str(e))
        subs = {r["url"] for r in db.all("SELECT p.url FROM podcasts p JOIN podcast_subs s ON s.podcast_id=p.id WHERE s.user_id=?",
                                         (req.user["id"],))}
        for r in results:
            r["image"] = img_url(r["image"])
            r["subscribed"] = r["feed"] in subs
        return {"results": results}

    @app.route("POST", "/api/podcasts")
    def subscribe(req):
        url = str(req.json().get("url") or "").strip()
        if re.match(r"^(?:itpc|pcast|podcast)://", url):
            url = "https://" + url.split("://", 1)[1]
        db.release()
        try:
            existing = db.one("SELECT id FROM podcasts WHERE url=?", (url,))
            if existing:
                pid = existing["id"]
            else:
                check_url(url, bool(req.user["is_admin"]))
                pid = pc.add_or_update(url, allow_private=bool(req.user["is_admin"]))[0]
        except PodcastError as e:
            raise HTTPError(400, str(e))
        db.run("INSERT OR IGNORE INTO podcast_subs(user_id, podcast_id, created_at) VALUES(?,?,?)",
               (req.user["id"], pid, int(time.time())))
        return podcast_card(db.one("SELECT * FROM podcasts WHERE id=?", (pid,)), req.user["id"])

    @app.route("GET", f"/api/podcasts/{PID}")
    def podcast(req):
        uid = req.user["id"]
        p = subscribed(uid, int(req.params["pid"]))
        eps = db.all("SELECT * FROM podcast_episodes WHERE podcast_id=? ORDER BY published DESC, id DESC LIMIT ?",
                     (p["id"], KEEP_EPISODES))
        prog = {r["episode_id"]: r for r in db.all("SELECT * FROM podcast_progress WHERE user_id=? AND episode_id IN "
                                                   "(SELECT id FROM podcast_episodes WHERE podcast_id=?)", (uid, p["id"]))}
        return {**podcast_card(p, uid), "episodes": [episode_card(e, prog.get(e["id"]), p) for e in eps]}

    @app.route("DELETE", f"/api/podcasts/{PID}")
    def unsubscribe(req):
        db.run("DELETE FROM podcast_subs WHERE user_id=? AND podcast_id=?", (req.user["id"], int(req.params["pid"])))
        return {"ok": True}

    @app.route("POST", f"/api/podcasts/{PID}/refresh")
    def refresh(req):
        p = subscribed(req.user["id"], int(req.params["pid"]))
        db.release()
        try:
            pc.add_or_update(p["url"])
        except PodcastError as e:
            raise HTTPError(502, str(e))
        return podcast(req)

    @app.route("POST", f"/api/podcasts/{PID}/played")
    def all_played(req):
        uid = req.user["id"]
        p = subscribed(uid, int(req.params["pid"]))
        now = int(time.time())
        db.run("INSERT INTO podcast_progress(user_id, episode_id, position_ms, duration_ms, played, updated_at) "
               "SELECT ?, id, 0, COALESCE(duration_ms,0), 1, ? FROM podcast_episodes WHERE podcast_id=? "
               "ON CONFLICT(user_id, episode_id) DO UPDATE SET played=1, position_ms=0", (uid, now, p["id"]))
        return {"ok": True}

    def episode_of(req):
        e = db.one("SELECT e.*, p.private FROM podcast_episodes e JOIN podcasts p ON p.id=e.podcast_id JOIN podcast_subs s "
                   "ON s.podcast_id=e.podcast_id WHERE e.id=? AND s.user_id=?", (int(req.params["eid"]), req.user["id"]))
        if not e:
            raise HTTPError(404, "Épisode introuvable")
        return e

    @app.route("POST", f"/api/podcasts/episodes/{EID}/progress")
    def ep_progress(req):
        e = episode_of(req)
        b = req.json()
        try:
            pos, dur = max(0, int(b.get("position") or 0)), max(0, int(b.get("duration") or 0))
        except (TypeError, ValueError):
            raise HTTPError(400, "Position invalide")
        played = 1 if b.get("played") or (dur and pos >= dur - 20000) else 0
        if "played" in b and not b["played"]:
            played, pos = 0, 0 if b.get("reset") else pos
        db.run("INSERT INTO podcast_progress(user_id, episode_id, position_ms, duration_ms, played, updated_at) VALUES(?,?,?,?,?,?) "
               "ON CONFLICT(user_id, episode_id) DO UPDATE SET position_ms=excluded.position_ms, "
               "duration_ms=CASE WHEN excluded.duration_ms>0 THEN excluded.duration_ms ELSE podcast_progress.duration_ms END, "
               "played=excluded.played, updated_at=excluded.updated_at",
               (req.user["id"], e["id"], 0 if played else pos, dur, played, int(time.time())))
        if dur and not e["duration_ms"]:
            db.run("UPDATE podcast_episodes SET duration_ms=? WHERE id=?", (dur, e["id"]))
        return {"ok": True, "played": bool(played)}

    @app.route("GET", f"/api/podcasts/episodes/{EID}/audio")
    def ep_audio(req):
        """L'audio, relayé : plages d'octets transmises (avance rapide), redirections vérifiées."""
        e = episode_of(req)
        db.release()
        headers = {}
        if req.headers.get("Range"):
            headers["Range"] = req.headers["Range"][:100]
        try:
            r = open_url(e["url"], headers, allow_private=bool(e["private"]), timeout=40)
        except urllib.error.HTTPError as err:
            if err.code == 416:
                return Response(b"", 416, "text/plain")
            raise HTTPError(502, f"Le serveur du podcast répond {err.code}")
        except (PodcastError, urllib.error.URLError, OSError) as err:
            raise HTTPError(502, f"Podcast injoignable : {getattr(err, 'reason', err)}")
        out = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
        if r.headers.get("Content-Range"):
            out["Content-Range"] = r.headers["Content-Range"]
        length = r.headers.get("Content-Length")
        ctype = r.headers.get("Content-Type") or e["mime"] or "audio/mpeg"
        if not ctype.startswith(("audio/", "video/", "application/octet-stream")):
            r.close()
            raise HTTPError(502, "Le fichier de l'épisode n'est pas de l'audio")

        def gen():
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                yield chunk
        return Response(stream=gen(), status=206 if r.status == 206 else 200, content_type=ctype,
                        length=int(length) if length and length.isdigit() else None, headers=out, on_close=r.close)

    @app.route("GET", "/api/podcasts/image")
    def image(req):
        u = req.arg("u") or ""
        known = db.one("SELECT private FROM podcasts WHERE image=? UNION ALL SELECT p.private FROM podcast_episodes e JOIN podcasts p "
                       "ON p.id=e.podcast_id WHERE e.image=? LIMIT 1", (u, u))
        if not known and u not in pc.search_images:
            raise HTTPError(404, "Image inconnue")
        db.release()
        try:
            path = pc.image(u, bool(known and known["private"]))
        except PodcastError:
            raise HTTPError(404, "Image indisponible")
        with open(path, "rb") as f:
            data = f.read()
        ctype = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[:4] == b"RIFF" else "image/jpeg"
        return Response(data, 200, ctype, {"Cache-Control": "private, max-age=604800"})
