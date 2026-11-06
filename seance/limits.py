"""Limites par compte, réglées par un administrateur (Paramètres → Utilisateurs) :

- lectures simultanées : au-delà, une nouvelle lecture est refusée (les autres continuent) ;
- débit hors du réseau local : depuis l'extérieur, la qualité est réduite pour tenir dans ce débit (utile
  pour ménager l'envoi de sa box, ou un compte prêté) ; à la maison, rien ne change ;
- temps d'écran (profils enfants surtout) : minutes par jour et heure du coucher. Le temps de lecture est
  compté pendant la lecture ; à la limite, la vidéo s'arrête avec un message, et une nouvelle lecture est
  refusée jusqu'au lendemain (ou jusqu'à la fin de la nuit).
"""
import re
import threading
import time

from . import dlna, transcoder

BEDTIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")
ACTIVE = 90          # un flux dont le lecteur a donné signe de vie dans les 90 s est en cours
MAX_GAP = 60         # au plus 60 s comptées entre deux comptes rendus de lecture (pause, veille…)
WARN_MINUTES = 5


def valid_limits(b):
    """Champs de limites envoyés par l'administration -> {colonne: valeur} (None : pas de limite)."""
    out = {}
    for k, lo, hi in (("max_streams", 1, 20), ("remote_kbps", 500, 200000), ("daily_minutes", 10, 1440)):
        if k in b:
            v = b[k]
            if v in (None, "", 0):
                out[k] = None
            elif isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
                out[k] = v
            else:
                raise ValueError(k)
    if "bedtime" in b:
        v = b["bedtime"]
        if v in (None, ""):
            out["bedtime"] = None
        elif isinstance(v, str) and BEDTIME.match(v) and v[:5] != v[6:]:
            out["bedtime"] = v
        else:
            raise ValueError("bedtime")
    return out


def _minutes(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def in_bedtime(bedtime, now=None):
    """« 21:00-07:00 » : vrai de 21 h à 7 h (la plage peut passer minuit)."""
    if not bedtime or not BEDTIME.match(bedtime):
        return False
    t = time.localtime(now or time.time())
    cur = t.tm_hour * 60 + t.tm_min
    start, end = _minutes(bedtime[:5]), _minutes(bedtime[6:])
    return start <= cur < end if start < end else cur >= start or cur < end


def today(now=None):
    return time.strftime("%Y-%m-%d", time.localtime(now or time.time()))


class Limits:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.Lock()
        self.last_report = {}  # (compte, flux) -> instant du dernier compte rendu de lecture

    # ------------------------------------------------------------ temps d'écran
    def used_today(self, uid):
        return self.ctx.db.scalar("SELECT seconds FROM screen_time WHERE user_id=? AND day=?", (uid, today())) or 0

    def screen(self, user):
        """État du temps d'écran d'un compte -> {limit, used, left, bedtime, blocked, reason} (minutes)."""
        limit = user.get("daily_minutes")
        bedtime = user.get("bedtime")
        used = self.used_today(user["id"]) // 60 if limit else 0
        out = {"limit": limit, "used": used, "left": max(0, limit - used) if limit else None, "bedtime": bedtime,
               "blocked": False, "reason": None}
        if bedtime and in_bedtime(bedtime):
            out.update(blocked=True, reason="bedtime")
        elif limit and used >= limit:
            out.update(blocked=True, reason="daily")
        return out

    def count(self, user, stream, state):
        """Compte rendu de lecture : ajoute le temps écoulé depuis le précédent -> état du temps d'écran, ou None
        (compte sans limite de temps)."""
        if not user.get("daily_minutes") and not user.get("bedtime"):
            return None
        now = time.time()
        key = (user["id"], stream or "")
        with self.lock:
            prev = self.last_report.get(key)
            self.last_report[key] = now
            if len(self.last_report) > 2000:
                for k, t in list(self.last_report.items()):
                    if now - t > 3600:
                        del self.last_report[k]
        if prev and state == "playing":
            delta = int(min(MAX_GAP, max(0, now - prev)))
            if delta:
                self.ctx.db.run("INSERT INTO screen_time(user_id, day, seconds) VALUES(?,?,?) ON CONFLICT(user_id, day) "
                                "DO UPDATE SET seconds=seconds+excluded.seconds", (user["id"], today(now), delta))
        return self.screen(user)

    # ------------------------------------------------------------ au lancement d'une lecture
    def check_play(self, user, replace=None):
        """Lève LimitError si ce compte ne peut pas lancer de lecture maintenant."""
        s = self.screen(user)
        if s["blocked"]:
            if s["reason"] == "bedtime":
                raise LimitError(403, "C'est l'heure d'aller dormir : la lecture reprendra à {end}.",
                                 end=user["bedtime"][6:])
            raise LimitError(403, "Temps d'écran du jour écoulé ({n} min) : à demain !", n=user["daily_minutes"])
        n = user.get("max_streams")
        if n:
            db = self.ctx.db
            busy = db.scalar("SELECT COUNT(*) FROM streams WHERE user_id=? AND last_seen>=? AND id<>?",
                             (user["id"], int(time.time()) - ACTIVE, str(replace or "")))
            if busy >= n:
                raise LimitError(429, "Ce compte regarde déjà {n} vidéo(s) en même temps : c'est la limite fixée par "
                                      "l'administrateur. Arrêtez une autre lecture pour lancer celle-ci.", n=n)

    def remote_cap(self, user, req):
        """Débit maximum (kbit/s) pour cette lecture, ou None : seulement hors du réseau local."""
        cap = user.get("remote_kbps")
        if not cap or dlna.is_local(req.client_ip):
            return None
        return cap


class LimitError(Exception):
    def __init__(self, status, message, **values):
        super().__init__(message)
        self.status = status
        self.message = message
        self.values = values


ORDER = ("original", "1080p", "720p", "480p")


def cap_quality(quality, probe, cap, file_size=0, duration_ms=0):
    """La plus haute des deux qualités ne doit pas dépasser ce que permet le débit maximum."""
    allowed = transcoder.auto_quality(probe, cap, file_size, duration_ms)
    return quality if ORDER.index(quality) >= ORDER.index(allowed) else allowed
