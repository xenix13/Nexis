"""Télécommande : un appareil qui lit une vidéo (ordinateur, TV, tablette) se pilote depuis un autre appareil
du même compte, typiquement le téléphone.

Le lecteur s'annonce par une connexion « Server-Sent Events » (il y reçoit les commandes) et envoie son état
(titre, position, lecture ou pause, pistes) quelques fois par minute et à chaque changement. La télécommande
liste les appareils du compte qui lisent quelque chose et leur envoie des commandes. Tout vit en mémoire.
"""
import json
import queue
import re
import threading
import time

DEVICE = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
ACTIONS = {"play", "pause", "toggle", "seek", "seekby", "next", "audio", "subtitle", "volume", "mute", "stop", "speed"}
GONE_AFTER = 45  # s sans nouvelles : l'appareil n'est plus listé
PING = 15


class Remote:
    def __init__(self):
        self.lock = threading.Lock()
        self.devices = {}   # (compte, appareil) -> {"name", "state", "queues", "seen"}
        self.watchers = {}  # compte -> files des télécommandes ouvertes (liste des appareils en direct)

    def _wake(self, uid):
        for q in list(self.watchers.get(uid, ())):
            try:
                q.put_nowait(1)
            except queue.Full:
                pass

    def attach(self, uid, device, name):
        q = queue.Queue(maxsize=50)
        with self.lock:
            d = self.devices.setdefault((uid, device), {"name": name, "state": None, "queues": [], "seen": time.time()})
            d["name"], d["seen"] = name, time.time()
            d["queues"].append(q)
        return q

    def detach(self, uid, device, q):
        with self.lock:
            d = self.devices.get((uid, device))
            if d and q in d["queues"]:
                d["queues"].remove(q)
                if not d["queues"]:
                    self.devices.pop((uid, device), None)
        self._wake(uid)

    def update(self, uid, device, state):
        with self.lock:
            d = self.devices.get((uid, device))
            if not d:
                return False
            d["state"], d["seen"] = state, time.time()
        self._wake(uid)
        return True

    def send(self, uid, device, cmd):
        with self.lock:
            d = self.devices.get((uid, device))
            queues = list(d["queues"]) if d else []
        for q in queues:
            try:
                q.put_nowait(cmd)
            except queue.Full:
                pass
        return bool(queues)

    def listing(self, uid, exclude=None):
        now = time.time()
        with self.lock:
            return [{"id": dev, "name": d["name"], "state": d["state"], "seen": int(d["seen"])}
                    for (u, dev), d in self.devices.items()
                    if u == uid and dev != exclude and d["state"] and now - d["seen"] < GONE_AFTER]

    def watch(self, uid):
        q = queue.Queue(maxsize=1)
        with self.lock:
            self.watchers.setdefault(uid, []).append(q)
        return q

    def unwatch(self, uid, q):
        with self.lock:
            lst = self.watchers.get(uid, [])
            if q in lst:
                lst.remove(q)


def register(app, ctx, x):
    from .web import HTTPError, Response
    remote = ctx.remote
    db = ctx.db

    def device_arg(req, value):
        dev = str(value or "")
        if not DEVICE.match(dev):
            raise HTTPError(400, "Appareil invalide")
        return dev

    def sse(gen, close):
        return Response(stream=gen, content_type="text/event-stream; charset=utf-8",
                        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}, on_close=close)

    @app.route("GET", "/api/remote/events")
    def remote_events(req):
        """Le lecteur s'annonce et reçoit ici les commandes de la télécommande."""
        uid = req.user["id"]
        dev = device_arg(req, req.arg("device"))
        name = str(req.arg("name") or "")[:60] or "Noxa"
        q = remote.attach(uid, dev, name)
        db.release()
        closed = threading.Event()

        def gen():
            yield b"retry: 3000\n\n"
            while not closed.is_set():
                try:
                    cmd = q.get(timeout=PING)
                except queue.Empty:
                    yield b": ping\n\n"
                    continue
                yield ("data: " + json.dumps(cmd, separators=(",", ":")) + "\n\n").encode()

        def close():
            closed.set()
            remote.detach(uid, dev, q)
        return sse(gen(), close)

    @app.route("POST", "/api/remote/state")
    def remote_state(req):
        b = req.json()
        dev = device_arg(req, b.get("device"))
        state = b.get("state")
        if state is not None and (not isinstance(state, dict) or len(json.dumps(state)) > 20000):
            raise HTTPError(400, "État invalide")
        if state is None:
            ok = remote.update(req.user["id"], dev, None)
        else:
            keep = ("item", "title", "subtitle", "thumb", "position", "duration", "playing", "audio", "subtitles",
                    "audio_selected", "subtitle_selected", "volume", "muted", "speed", "has_next")
            ok = remote.update(req.user["id"], dev, {k: state.get(k) for k in keep})
        return {"ok": ok}

    @app.route("GET", "/api/remote/devices")
    def remote_devices(req):
        return remote.listing(req.user["id"], req.arg("exclude"))

    @app.route("GET", "/api/remote/watch")
    def remote_watch(req):
        """Liste en direct des appareils qui lisent quelque chose (page Télécommande)."""
        uid = req.user["id"]
        exclude = req.arg("exclude")
        q = remote.watch(uid)
        db.release()
        closed = threading.Event()

        def gen():
            last = None
            yield b"retry: 3000\n\n"
            while not closed.is_set():
                data = json.dumps(remote.listing(uid, exclude), separators=(",", ":"))
                if data != last:
                    last = data
                    yield ("data: " + data + "\n\n").encode()
                try:
                    q.get(timeout=5)  # changement signalé, sinon un coup d'œil toutes les 5 s (appareils disparus)
                except queue.Empty:
                    yield b": ping\n\n"

        def close():
            closed.set()
            remote.unwatch(uid, q)
        return sse(gen(), close)

    @app.route("POST", r"/api/remote/(?P<dev>[A-Za-z0-9_-]{8,40})/cmd")
    def remote_cmd(req):
        b = req.json()
        action = b.get("action")
        if action not in ACTIONS:
            raise HTTPError(400, "Commande inconnue")
        cmd = {"action": action}
        for k in ("value",):
            v = b.get(k)
            if v is not None:
                if not isinstance(v, (int, float, str)) or isinstance(v, bool) or len(str(v)) > 40:
                    raise HTTPError(400, "Valeur invalide")
                cmd[k] = v
        if not remote.send(req.user["id"], req.params["dev"], cmd):
            raise HTTPError(404, "Cet appareil ne lit plus rien")
        return {"ok": True}
