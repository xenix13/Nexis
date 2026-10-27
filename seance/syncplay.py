"""« Regarder ensemble » : lecture synchronisée à plusieurs.

Un groupe partage UNE position de lecture : « en lecture depuis tel instant à telle position », ou « en
pause à telle position ». Chaque membre a son propre flux (sa qualité, ses sous-titres) et cale sa vidéo
sur cette position ; un petit écart est rattrapé en accélérant ou ralentissant à peine la lecture, un
grand écart par un saut.

Quand quelqu'un met en pause, avance, change d'épisode ou que sa connexion décroche, tout le groupe
attend que chacun ait assez de vidéo en réserve, puis repart en même temps.

Les événements arrivent aux navigateurs par « Server-Sent Events » (une connexion HTTP ouverte), sans
dépendance. Les groupes vivent en mémoire : un redémarrage du serveur les dissout.
"""
import json
import logging
import queue
import secrets
import threading
import time

from .i18n import tr

log = logging.getLogger("seance.sync")

WAIT_TIMEOUT = 12        # s : on repart sans le membre trop lent
RELOAD_TIMEOUT = 60      # s : un membre qui change de langue ou de qualité (extraction, relance de ffmpeg)
GONE_AFTER = 40          # s sans connexion : le membre a quitté
EMPTY_AFTER = 120        # s : groupe vide supprimé
PRESENT_FOR = 1800       # s : Nexis ouvert ailleurs garde membre d'un groupe, pas plus (salon oublié sur une TV)
MAX_STREAMS_PER_USER = 6  # connexions d'événements ouvertes en même temps par une personne (onglets, appareils)
FEED_PING = 10           # s : ping de la liste en direct (détecte aussi un onglet fermé ou une session finie)
MAX_FEEDS_PER_USER = 8    # listes de groupes en direct ouvertes en même temps par une personne (onglets, appareils)


def now_ms():
    return int(time.time() * 1000)


def _translated(data):
    """Événement déjà prêt à envoyer, avec ses messages dans la langue de la personne qui le reçoit."""
    try:
        event = json.loads(data[len(b"data: "):].decode("utf-8"))
    except ValueError:
        return data
    for k in ("notice", "text"):
        if event.get(k):
            event[k] = tr(event[k])
    return ("data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode()


class Member:
    def __init__(self, user):
        self.uid = user["id"]
        self.name = user["display_name"]
        self.color = user["avatar_color"]
        ver = user.get("avatar_ver") if hasattr(user, "get") else None
        self.avatar = f"/api/users/{user['id']}/avatar?v={ver}" if ver else None
        self.joined = time.time()
        self.connections = 0
        self.seen = time.time()
        self.ready = False


class Group:
    def __init__(self, gid, owner, name, host_only, present=frozenset):
        self.id = gid
        self.owner = owner["id"]
        self.owner_name = owner["display_name"]
        self.name = name
        self.host_only = host_only
        self.created = time.time()
        self.members = {}
        self.item_id = None
        self.playing = False
        self.position = 0
        self.ref = now_ms()
        self.waiting = set()
        self.wait_timer = None
        self.subscribers = []
        self.seq = 0
        self.empty_since = None
        self.present = present  # personnes qui ont Nexis ouvert (liste des groupes en direct)

    def position_now(self):
        if self.playing and not self.waiting:
            return self.position + (now_ms() - self.ref)
        return self.position

    def state(self, action=None, by=None, notice=None):
        return {"type": "state", "group": self.id, "item_id": str(self.item_id) if self.item_id else None,
                "playing": self.playing, "waiting": bool(self.waiting),
                "waiting_for": [self.members[u].name for u in self.waiting if u in self.members],
                "waiting_ids": sorted(self.waiting),
                "position": int(self.position), "ref": self.ref, "server_now": now_ms(), "seq": self.seq,
                "action": action, "by": by, "notice": notice}

    def members_event(self):
        return {"type": "members", "server_now": now_ms(), "host_only": self.host_only, "name": self.name,
                "members": [{"id": m.uid, "name": m.name, "color": m.color, "avatar": m.avatar, "ready": m.ready, "host": m.uid == self.owner,
                             "online": m.connections > 0 or m.uid in self.present()} for m in self.members.values()]}


class Groups:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.RLock()
        self.groups = {}
        self.watchers = {}  # file d'attente -> id de la personne (liste des groupes en direct)

    # ------------------------------------------------------------ liste en direct
    def present(self):
        return set(self.watchers.values())

    def watch(self, user):
        q = queue.Queue(maxsize=1)
        with self.lock:
            if sum(1 for uid in self.watchers.values() if uid == user["id"]) >= MAX_FEEDS_PER_USER:
                raise OverflowError("Trop de connexions ouvertes : fermez d'autres onglets de Nexis")
            self.watchers[q] = user["id"]
        return q

    def unwatch(self, q):
        with self.lock:
            uid = self.watchers.pop(q, None)
            now = time.time()
            for g in self.groups.values():  # le délai avant de quitter le groupe part de maintenant
                m = g.members.get(uid)
                if m:
                    m.seen = now

    def _changed(self):
        """La liste des groupes a changé : on réveille les listes ouvertes (une notification suffit)."""
        for q in list(self.watchers):
            try:
                q.put_nowait(1)
            except queue.Full:
                pass

    # ------------------------------------------------------------ diffusion
    def _send(self, g, event):
        data = ("data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode()
        for q in list(g.subscribers):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass

    def _broadcast_state(self, g, action=None, by=None, notice=None):
        g.seq += 1
        self._send(g, g.state(action, by, notice))

    # ------------------------------------------------------------ groupes
    def create(self, user, name=None, item_id=None, position=0, host_only=False):
        with self.lock:
            if len([g for g in self.groups.values() if g.owner == user["id"]]) >= 3:
                raise ValueError("Limite atteinte : 3 groupes animés à la fois au maximum")
            gid = secrets.token_urlsafe(6)
            g = Group(gid, user, (name or f"Chez {user['display_name']}")[:60], host_only, self.present)
            if item_id:
                g.item_id = int(item_id)
                g.position = max(0, int(position or 0))
                g.playing = True
                g.waiting = {user["id"]}  # on part dès que l'hôte a chargé la vidéo
            self.groups[gid] = g
            self._join(g, user)
            self._changed()
            return g

    def get(self, gid):
        with self.lock:
            return self.groups.get(gid)

    def _join(self, g, user):
        m = g.members.get(user["id"])
        if not m:
            m = g.members[user["id"]] = Member(user)
        m.seen = time.time()
        g.empty_since = None
        return m

    def join(self, gid, user):
        with self.lock:
            g = self.groups.get(gid)
            if not g:
                return None
            new = user["id"] not in g.members
            self._join(g, user)
            if new:
                self._changed()
                self._send(g, g.members_event())
                self._send(g, {"type": "notice", "text": f"{user['display_name']} a rejoint le groupe", "server_now": now_ms()})
            return g

    def leave(self, gid, user):
        with self.lock:
            g = self.groups.get(gid)
            if not g or user["id"] not in g.members:
                return
            self._remove(g, user["id"], f"{user['display_name']} a quitté le groupe")

    def _remove(self, g, uid, text):
        g.members.pop(uid, None)
        if uid in g.waiting:
            g.waiting.discard(uid)
            self._maybe_resume(g)
        if g.owner == uid and g.members:
            heir = min(g.members.values(), key=lambda m: m.joined)
            g.owner, g.owner_name = heir.uid, heir.name
            text += f" ; {heir.name} devient l'hôte"
        if not g.members:
            g.empty_since = time.time()
        self._changed()
        self._send(g, g.members_event())
        self._send(g, {"type": "notice", "text": text, "server_now": now_ms()})

    def update(self, gid, user, name=None, host_only=None):
        with self.lock:
            g = self.groups.get(gid)
            if not g:
                return None
            if g.owner != user["id"]:
                raise PermissionError("Seul l'hôte peut modifier le groupe")
            if name:
                g.name = name[:60]
            if host_only is not None:
                g.host_only = bool(host_only)
            self._changed()
            self._send(g, g.members_event())
            return g

    # ------------------------------------------------------------ commandes
    def _wait_for(self, g, uids, timeout=WAIT_TIMEOUT):
        g.waiting = set(uids) & set(g.members)
        if g.wait_timer:
            g.wait_timer.cancel()
        if g.waiting:
            seq = g.seq + 1
            g.wait_timer = threading.Timer(timeout, self._timeout, args=(g.id, seq))
            g.wait_timer.daemon = True
            g.wait_timer.start()

    def _timeout(self, gid, seq):
        with self.lock:
            g = self.groups.get(gid)
            if not g or not g.waiting or g.seq != seq:
                return
            late = ", ".join(g.members[u].name for u in g.waiting if u in g.members)
            g.waiting = set()
            g.ref = now_ms()
            self._broadcast_state(g, "resume", None, f"On repart sans attendre {late}" if late else None)

    def _maybe_resume(self, g):
        if not g.waiting:
            if g.wait_timer:
                g.wait_timer.cancel()
                g.wait_timer = None
            g.ref = now_ms()
            self._broadcast_state(g, "resume")

    def command(self, gid, user, action, position=None, item_id=None, reason=None):
        with self.lock:
            g = self.groups.get(gid)
            if not g:
                raise LookupError("Ce groupe n'existe plus")
            m = g.members.get(user["id"])
            if not m:
                raise PermissionError("Vous ne faites pas partie de ce groupe")
            m.seen = time.time()
            control = action in ("play", "pause", "seek", "item")
            if control and g.host_only and g.owner != user["id"]:
                raise PermissionError("Seul l'hôte contrôle la lecture dans ce groupe")
            pos = max(0, int(position)) if position is not None else None
            others = [u for u in g.members if u != user["id"]]
            if action == "play":
                g.position = pos if pos is not None else g.position_now()
                g.playing = True
                g.ref = now_ms()
                g.waiting = set()
                self._broadcast_state(g, "play", m.name)
            elif action == "pause":
                g.position = pos if pos is not None else g.position_now()
                g.playing = False
                g.waiting = set()
                self._broadcast_state(g, "pause", m.name)
            elif action == "seek":
                g.position = pos if pos is not None else g.position_now()
                g.ref = now_ms()
                if g.playing:
                    self._wait_for(g, others)  # chacun recharge à la nouvelle position, puis on repart ensemble
                self._broadcast_state(g, "seek", m.name)
            elif action == "item":
                if g.item_id == int(item_id) and g.waiting:
                    return g  # même changement demandé par plusieurs membres (fin d'épisode)
                g.item_id = int(item_id)
                g.position = pos or 0
                g.playing = True
                g.ref = now_ms()
                for mm in g.members.values():
                    mm.ready = False
                self._wait_for(g, list(g.members))
                self._broadcast_state(g, "item", m.name)
            elif action == "buffering":
                if m.ready:
                    m.ready = False
                    self._send(g, g.members_event())
                if reason == "reload" and g.playing:
                    # changement de langue, de sous-titres incrustés ou de qualité : tout le groupe attend
                    # ce membre, à l'endroit où il en était
                    g.position = g.position_now() if g.waiting or pos is None else pos
                    g.ref = now_ms()
                    self._wait_for(g, set(g.waiting) | {user["id"]}, RELOAD_TIMEOUT)
                    self._broadcast_state(g, "wait", m.name, f"{m.name} recharge la vidéo : pause le temps qu'il soit prêt")
                elif g.playing and not g.waiting:
                    g.position = g.position_now()
                    self._wait_for(g, [user["id"]])
                    self._broadcast_state(g, "wait", m.name)
            elif action == "ready":
                if not m.ready:
                    m.ready = True
                    self._send(g, g.members_event())
                if user["id"] in g.waiting:
                    g.waiting.discard(user["id"])
                    self._maybe_resume(g)
            else:
                raise ValueError("Action inconnue")
            if action in ("play", "pause", "item"):
                self._changed()
            return g

    # ------------------------------------------------------------ événements
    def subscribe(self, g, user):
        """Ouvre un flux d'événements pour un MEMBRE du groupe (rejoindre passe par POST …/join :
        une simple requête GET, qu'un autre site peut provoquer, ne fait entrer personne dans un groupe)."""
        q = queue.Queue(maxsize=200)
        with self.lock:
            m = g.members.get(user["id"])
            if not m:
                raise PermissionError("Rejoignez d'abord le groupe")
            opened = sum(mm.connections for gg in self.groups.values() for uid, mm in gg.members.items()
                         if uid == user["id"])
            if opened >= MAX_STREAMS_PER_USER:
                raise OverflowError("Trop de connexions ouvertes : fermez d'autres onglets du lecteur")
            m.seen = time.time()
            m.connections += 1
            g.subscribers.append(q)
            first = m.connections == 1
        if first:
            self._send(g, g.members_event())
        else:
            q.put_nowait(("data: " + json.dumps(g.members_event()) + "\n\n").encode())
        q.put_nowait(("data: " + json.dumps(g.state("sync")) + "\n\n").encode())
        return q

    def unsubscribe(self, g, user, q):
        with self.lock:
            if q in g.subscribers:
                g.subscribers.remove(q)
            m = g.members.get(user["id"])
            if m:
                m.connections = max(0, m.connections - 1)
                m.seen = time.time()
                if not m.connections:
                    self._send(g, g.members_event())

    def reap(self):
        now = time.time()
        with self.lock:
            present = self.present()
            for g in list(self.groups.values()):
                for uid, m in list(g.members.items()):
                    # on reste membre tant que Nexis est ouvert (on cherche un film pendant que les autres attendent)
                    away = now - m.seen
                    if not m.connections and away > GONE_AFTER and (uid not in present or away > PRESENT_FOR):
                        self._remove(g, uid, f"{m.name} a quitté le groupe")
                if not g.members and now - (g.empty_since or now) > EMPTY_AFTER:
                    self._send(g, {"type": "closed", "server_now": now_ms()})
                    self.groups.pop(g.id, None)
                    self._changed()

    def listing(self):
        with self.lock:
            return [{"id": g.id, "name": g.name, "owner": g.owner_name, "owner_id": g.owner, "item_id": g.item_id, "playing": g.playing,
                     "host_only": g.host_only, "members": [m.name for m in g.members.values()],
                     "member_ids": list(g.members)} for g in self.groups.values() if g.members]


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    from .web import HTTPError, Response
    db = ctx.db
    groups = ctx.together
    GID = r"(?P<gid>[A-Za-z0-9_-]{6,12})"

    def item_card(user, item_id):
        if not item_id:
            return None
        row = db.one(x.ITEM_SQL + "WHERE i.id=?", (int(item_id),))
        if not row or str(row["library_id"]) not in x.allowed_set(user) or not x.visible(user, row):
            return {"id": str(item_id), "hidden": True, "title": "Contenu non accessible avec ce profil"}
        return x.card(row)

    def view(g, user):
        v = {"id": g.id, "name": g.name, "owner": g.owner_name, "is_host": g.owner == user["id"],
             "host_only": g.host_only, "item": item_card(user, g.item_id), "member": user["id"] in g.members}
        v.update(g.state("sync"))
        v.update(members=g.members_event()["members"])
        return v

    def group_or_404(gid):
        g = groups.get(gid)
        if not g:
            raise HTTPError(404, "Ce groupe n'existe plus")
        return g

    def listing_for(user):
        out = []
        for g in groups.listing():
            g = dict(g)
            g["item"] = item_card(user, g.pop("item_id"))
            g["member"] = user["id"] in g.pop("member_ids")
            g["is_host"] = g.pop("owner_id") == user["id"]
            out.append(g)
        return out

    @app.route("GET", "/api/together")
    def together_list(req):
        return listing_for(req.user)

    @app.route("GET", "/api/together/feed")
    def together_feed(req):
        """Liste des groupes en direct (Server-Sent Events) : accueil, page des groupes, barre du haut."""
        user = dict(req.user)
        session = req.session_hash
        try:
            q = groups.watch(user)
        except OverflowError as e:
            raise HTTPError(429, str(e))
        closed = threading.Event()

        def snapshot():
            try:
                return ("data: " + json.dumps({"type": "groups", "groups": listing_for(user)}, ensure_ascii=False,
                                              separators=(",", ":")) + "\n\n").encode()
            finally:
                db.release()  # pas de connexion à la base gardée pendant toute la durée du flux

        def signed_in():
            try:
                return bool(db.scalar("SELECT 1 FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? "
                                      "AND s.expires_at>? AND NOT u.disabled", (session, int(time.time()))))
            finally:
                db.release()

        def gen():
            last = snapshot()
            yield b"retry: 3000\n\n" + last
            while not closed.is_set():
                try:
                    q.get(timeout=FEED_PING)
                except queue.Empty:
                    if not signed_in():  # déconnexion ou compte supprimé : on coupe (la reconnexion recevra 401)
                        return
                    yield b": ping\n\n"
                    continue
                if not signed_in():
                    return
                closed.wait(0.25)  # plusieurs changements d'un coup : un seul envoi
                data = snapshot()
                if data != last:
                    last = data
                    yield data

        def close():
            closed.set()
            groups.unwatch(q)
        db.release()
        return Response(stream=gen(), content_type="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}, on_close=close)

    @app.route("GET", "/api/together/time")
    def together_time(req):
        return {"now": now_ms()}

    @app.route("POST", "/api/together")
    def together_create(req):
        b = req.json()
        item_id = b.get("item_id")
        if item_id is not None:
            r = x.require_item(req.user, str(item_id))
            if r["type"] not in ("movie", "episode", "extra"):
                raise HTTPError(400, "Sélectionnez un film ou un épisode")
        name = str(b.get("name") or "").strip()
        try:
            g = groups.create(req.user, name, item_id, b.get("position") or 0, bool(b.get("host_only")))
        except ValueError as e:
            raise HTTPError(409, str(e))
        return view(g, req.user)

    @app.route("GET", f"/api/together/{GID}")
    def together_get(req):
        return view(group_or_404(req.params["gid"]), req.user)

    @app.route("PUT", f"/api/together/{GID}")
    def together_update(req):
        b = req.json()
        try:
            g = groups.update(req.params["gid"], req.user, str(b.get("name") or "").strip() or None, b.get("host_only"))
        except PermissionError as e:
            raise HTTPError(403, str(e))
        if not g:
            raise HTTPError(404, "Ce groupe n'existe plus")
        return view(g, req.user)

    @app.route("POST", f"/api/together/{GID}/join")
    def together_join(req):
        g = groups.join(req.params["gid"], req.user)
        if not g:
            raise HTTPError(404, "Ce groupe n'existe plus")
        return view(g, req.user)

    @app.route("POST", f"/api/together/{GID}/leave")
    def together_leave(req):
        groups.leave(req.params["gid"], req.user)
        return {"ok": True}

    @app.route("POST", f"/api/together/{GID}/cmd")
    def together_cmd(req):
        b = req.json()
        action = b.get("action")
        item_id = b.get("item_id")
        if action == "item":
            if not str(item_id or "").isdigit():
                raise HTTPError(400, "Élément invalide")
            r = x.require_item(req.user, str(item_id))
            if r["type"] not in ("movie", "episode", "extra"):
                raise HTTPError(400, "Élément non lisible")
        pos = b.get("position")
        if pos is not None and not isinstance(pos, (int, float)):
            raise HTTPError(400, "Position invalide")
        try:
            g = groups.command(req.params["gid"], req.user, action, pos, item_id,
                               "reload" if b.get("reason") == "reload" else None)
        except LookupError as e:
            raise HTTPError(404, str(e))
        except PermissionError as e:
            raise HTTPError(403, str(e))
        except ValueError as e:
            raise HTTPError(400, str(e))
        return g.state()

    @app.route("GET", f"/api/together/{GID}/events")
    def together_events(req):
        g = group_or_404(req.params["gid"])
        user = dict(req.user)
        try:
            q = groups.subscribe(g, user)
        except PermissionError as e:
            raise HTTPError(403, str(e))
        except OverflowError as e:
            raise HTTPError(429, str(e))
        db.release()
        closed = threading.Event()

        def gen():
            yield b"retry: 3000\n\n"
            while not closed.is_set():
                try:
                    data = q.get(timeout=15)
                except queue.Empty:
                    if groups.get(g.id) is not g:
                        yield b'data: {"type":"closed"}\n\n'
                        return
                    data = b": ping\n\n"
                if b'"notice":"' in data or b'"type":"notice"' in data:
                    data = _translated(data)
                yield data
                if data.startswith(b'data: {"type":"closed"'):
                    return

        def close():
            closed.set()
            groups.unsubscribe(g, user, q)
        return Response(stream=gen(), content_type="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}, on_close=close)
