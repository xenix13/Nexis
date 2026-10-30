"""Préparation à l'avance : sous-titres internes et vignettes d'aperçu (barre de lecture).

Extraire une piste de sous-titres d'un MKV oblige ffmpeg à lire tout le fichier : sur un
partage réseau, cela peut prendre une à plusieurs minutes. On extrait donc toutes les pistes
texte d'un fichier en UNE seule lecture, et le plus tôt possible :
- en priorité, à pleine vitesse, avant de lancer une lecture (le lecteur attend que tout soit prêt) ;
- en urgence pour l'épisode suivant, avec un débit de lecture plafonné (-readrate) pour ne pas
  priver le flux en cours de bande passante sur le partage réseau ;
- en tâche de fond (avec les vignettes) uniquement quand aucune lecture n'est en cours : une
  tâche de fond s'interrompt dès qu'une lecture démarre et reprendra plus tard.
"""
import collections
import contextlib
import json
import math
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

from . import media
from .fonts import font_names
from .subtitles import parse_subtitles

log = logging.getLogger("seance.subs")


class Job:
    def __init__(self, item_id, kind="play"):
        self.item_id = item_id
        self.kind = kind          # play | soon (plafonné) | idle (avec vignettes) | gentle (fond pendant une lecture)
        self.progress = 0.0
        self.until_ms = 0         # instant jusqu'où les sous-titres sont déjà extraits (lecture progressive)
        self.tmp = None           # dossier où ffmpeg écrit au fil de l'eau
        self.done = threading.Event()
        self.error = None
        self.aborted = False


class Aborted(Exception):
    pass


SOON_READRATE = 8     # x temps réel : ~40 Mb/s pour un fichier à 5 Mb/s
GENTLE_READRATE = 2   # préparation de fond pendant qu'on regarde : très peu de bande passante
PARTIAL_MARGIN_MS = 5000


TRICK_INTERVAL = 10   # une vignette toutes les 10 s
TRICK_W = 240
TRICK_COLS = TRICK_ROWS = 10


ASS_CODECS = {"ass", "ssa"}


def text_tracks(probe):
    return [s["index"] for s in (probe or {}).get("subs", []) if s.get("codec") in media.TEXT_SUB_CODECS]


PGS_CODECS = {"hdmv_pgs_subtitle", "pgssub"}


def pgs_tracks(probe):
    return [s["index"] for s in (probe or {}).get("subs", []) if s.get("codec") in PGS_CODECS]


def ass_tracks(probe):
    return [s["index"] for s in (probe or {}).get("subs", []) if s.get("codec") in ASS_CODECS]


def _complete_sup(data):
    """Coupe un .sup en cours d'écriture après le dernier « display set » complet (segment END)."""
    i, end = 0, 0
    while i + 13 <= len(data) and data[i:i + 2] == b"PG":
        size = int.from_bytes(data[i + 11:i + 13], "big")
        if i + 13 + size > len(data):
            break
        if data[i + 10] == 0x80:
            end = i + 13 + size
        i += 13 + size
    return data[:end]


def _probe(item):
    return (json.loads(item["probe"] or "{}") if isinstance(item["probe"], str) else item["probe"]) or {}


class SubtitleCache:
    def __init__(self, ctx):
        self.ctx = ctx
        self.base = os.path.join(ctx.data_dir, "cache", "subs")
        self.trick_base = os.path.join(ctx.data_dir, "cache", "trickplay")
        os.makedirs(self.base, exist_ok=True)
        os.makedirs(self.trick_base, exist_ok=True)
        self.lock = threading.Lock()
        self.jobs = {}
        self.urgent = collections.OrderedDict()
        self.idle = collections.OrderedDict()
        self.slots = threading.BoundedSemaphore(2)
        self.wake = threading.Event()
        self.stopped = threading.Event()
        threading.Thread(target=self._worker, daemon=True).start()

    # ------------------------------------------------------------ cache
    @staticmethod
    def content_key(item):
        """Le cache suit le FICHIER (taille + date), pas sa fiche : un fichier renommé, déplacé ou réindexé
        retrouve ses sous-titres et ses vignettes déjà exportés, sans relire le fichier."""
        size = item["file_size"] if "file_size" in item.keys() else None
        return f"f{size}_{item['file_mtime'] or 0}" if size else None

    def _keyed(self, base, item):
        key = self.content_key(item)
        legacy = os.path.join(base, f"{item['id']}_{item['file_mtime'] or 0}")
        if not key:
            return legacy
        path = os.path.join(base, key)
        if not os.path.isdir(path) and os.path.isdir(legacy):
            try:  # cache d'une version précédente (rangé par numéro de fiche) : on le reprend tel quel
                os.replace(legacy, path)
            except OSError:
                return legacy
        return path

    def _dir(self, item):
        return self._keyed(self.base, item)

    def path(self, item, index):
        return os.path.join(self._dir(item), f"{int(index)}.json")

    def sup_path(self, item, index):
        return os.path.join(self._dir(item), f"{int(index)}.sup")

    def ass_path(self, item, index):
        return os.path.join(self._dir(item), f"{int(index)}.ass")

    def fonts_index(self, item):
        try:
            with open(os.path.join(self._dir(item), "fonts.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def font_path(self, item, name):
        return os.path.join(self._dir(item), "fonts", os.path.basename(name))

    def missing(self, item):
        """Pistes à extraire : texte (JSON), ASS d'origine (rendu fidèle) et polices jointes."""
        if item.get("peer_id"):
            return []  # élément d'un serveur lié : préparé par ce serveur
        probe = _probe(item)
        ass = ass_tracks(probe)
        need = [i for i in text_tracks(probe) if not os.path.isfile(self.path(item, i))
                or (i in ass and not os.path.isfile(self.ass_path(item, i)) and not self._failed(item, i))]
        # PGS (Blu-ray) : flux .sup d'origine, rendu dans le navigateur plutôt qu'incrusté
        need += [i for i in pgs_tracks(probe) if not os.path.isfile(self.sup_path(item, i))
                 and not os.path.isfile(self.path(item, i))]
        if ass and self.fonts_index(item) is None:
            need = sorted(set(need) | set(ass))
        return need

    def _failed(self, item, index):
        try:
            with open(self.path(item, index), encoding="utf-8") as f:
                return bool(json.load(f).get("error"))
        except (OSError, ValueError):
            return False

    # -- vignettes d'aperçu
    def trick_dir(self, item):
        return self._keyed(self.trick_base, item)

    def trick_index(self, item):
        try:
            with open(os.path.join(self.trick_dir(item), "index.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def needs_trick(self, item):
        if item.get("peer_id") or self.ctx.db.setting("trickplay", "1") != "1" or media.is_strm(item["key"]):
            return False
        probe = json.loads(item["probe"] or "{}") if isinstance(item["probe"], str) else item["probe"]
        if not (probe or {}).get("video") or (probe.get("duration") or 0) < 60:
            return False
        return self.trick_index(item) is None

    def has_work(self, item):
        return bool(self.missing(item)) or self.needs_trick(item)

    def ready(self, item):
        """Toutes les pistes texte internes sont-elles extraites ?"""
        return not self.missing(item)

    def read(self, item, index):
        try:
            with open(self.path(item, index), "rb") as f:
                return f.read()
        except OSError:
            return None

    def read_sup(self, item, index):
        try:
            with open(self.sup_path(item, index), "rb") as f:
                return f.read()
        except OSError:
            return None

    def read_ass(self, item, index):
        try:
            with open(self.ass_path(item, index), "rb") as f:
                return f.read()
        except OSError:
            return None

    def status(self, item):
        with self.lock:
            job = self.jobs.get(item["id"])
        if job and not job.done.is_set():
            return {"state": "running", "progress": round(job.progress, 3)}
        if job and job.error:
            return {"state": "error", "error": job.error}
        if self.missing(item):
            queued = item["id"] in self.urgent or item["id"] in self.idle
            return {"state": "queued" if queued else "missing", "progress": 0}
        return {"state": "ready", "progress": 1}

    # ------------------------------------------------------------ demandes
    def ensure_now(self, item):
        """Extraction prioritaire (lancement d'une lecture). Renvoie le Job (ou None si tout est prêt)."""
        if not self.missing(item):
            return None
        with self.lock:
            job = self.jobs.get(item["id"])
            if job and not job.done.is_set():
                if job.kind not in ("idle", "gentle"):
                    return job
                job.aborted = True  # une tâche de fond : on la remplace par une extraction prioritaire
            job = Job(item["id"], "play")
            self.jobs[item["id"]] = job
            self.urgent.pop(item["id"], None)
            self.idle.pop(item["id"], None)
        row = dict(item)
        threading.Thread(target=self._run_slot, args=(row, job), daemon=True).start()
        return job

    def enqueue(self, item_id, urgent=False):
        with self.lock:
            job = self.jobs.get(item_id)
            if job and not job.done.is_set():
                if not (urgent and job.kind in ("idle", "gentle")):
                    return
                job.aborted = True  # on la relance en priorité
            if urgent:
                self.idle.pop(item_id, None)
                self.urgent[item_id] = True
                self.urgent.move_to_end(item_id)
            elif item_id not in self.urgent:
                self.idle[item_id] = True
        self.wake.set()

    def wait(self, job, timeout):
        return job.done.wait(timeout)

    # ------------------------------------------------------------ lecture progressive
    def running(self, item):
        with self.lock:
            job = self.jobs.get(item["id"])
        return job if job and not job.done.is_set() else None

    def partial(self, item, index, ext):
        """Pendant l'extraction : (données déjà extraites, jusqu'à quel instant en ms) ou None."""
        job = self.running(item)
        if not job or not job.tmp:
            return None
        try:
            with open(os.path.join(job.tmp, f"{int(index)}.{ext}"), "rb") as f:
                data = f.read()
        except OSError:
            return None
        if ext == "srt":
            data = data[:data.rfind(b"\n\n") + 2] if b"\n\n" in data else b""
        elif ext == "ass":
            data = data[:data.rfind(b"\n") + 1]
        elif ext == "sup":
            data = _complete_sup(data)
        return data, job.until_ms

    # ------------------------------------------------------------ travail
    def _streaming(self):
        try:
            return bool(self.ctx.db.scalar("SELECT COUNT(*) FROM streams WHERE last_seen > ?", (int(time.time()) - 90,)))
        except Exception:
            return False

    def _next(self):
        with self.lock:
            if self.urgent:
                return self.urgent.popitem(last=False)[0], "soon"
        streaming = self._streaming()
        with self.lock:
            if self.idle:
                # pendant une lecture, la file de fond avance quand même, doucement et un fichier à la fois
                if streaming and any(j.kind == "gentle" and not j.done.is_set() for j in self.jobs.values()):
                    return None, None
                return self.idle.popitem(last=False)[0], ("gentle" if streaming else "idle")
        return None, None

    def _worker(self):
        while not self.stopped.is_set():
            self.wake.wait(10)
            self.wake.clear()
            while not self.stopped.is_set():
                item_id, kind = self._next()
                if item_id is None:
                    break
                try:
                    row = self.ctx.db.one("SELECT id, key, probe, file_size, file_mtime, duration_ms FROM items WHERE id=?", (item_id,))
                    if not row:
                        continue
                    todo = self.missing(row) if kind in ("soon", "gentle") else self.has_work(row)
                    if not todo:
                        if kind == "soon" and self.needs_trick(row):
                            self.enqueue(item_id)
                        if kind == "gentle" and self.needs_trick(row):
                            with self.lock:  # vignettes seulement : on attend que plus personne ne regarde
                                self.idle[item_id] = True
                            break
                        continue
                finally:
                    self.ctx.db.release()
                prev = self.jobs.get(item_id)
                if prev and prev.aborted:
                    prev.done.wait(10)  # tâche de fond interrompue pour être relancée en priorité
                with self.lock:
                    if item_id in self.jobs and not self.jobs[item_id].done.is_set():
                        continue
                    job = Job(item_id, kind)
                    self.jobs[item_id] = job
                self._run_slot(row, job)

    def _run_slot(self, item, job):
        # une lecture qui attend ne fait jamais la queue derrière les tâches de fond
        slot = contextlib.nullcontext() if job.kind == "play" else self.slots
        aborted = False
        with slot:
            try:
                self._extract(item, job)
            except Aborted:
                aborted = True
                log.debug("Préparation de %s interrompue (lecture en cours)", item.get("key"))
            except Exception as e:
                log.warning("Sous-titres de %s : %s", item.get("key"), e)
                job.error = str(e)
            finally:
                job.progress = 1.0 if not job.error else job.progress
                with self.lock:
                    # on garde les jobs terminés en erreur un moment pour l'afficher, pas les autres
                    if not job.error and self.jobs.get(item["id"]) is job:
                        self.jobs.pop(item["id"], None)
                job.done.set()
        try:
            if aborted and job.kind == "idle" and not job.aborted:
                self.enqueue(item["id"])  # reprendra quand plus personne ne regarde
            elif job.kind != "idle" and not job.error and self.needs_trick(item):
                self.enqueue(item["id"])  # vignettes plus tard, en tâche de fond
        finally:
            self.ctx.db.release()

    def _extract(self, item, job):
        tracks = self.missing(item)
        trick = job.kind == "idle" and self.needs_trick(item)
        if not tracks and not trick:
            return
        if not item["key"] or not os.path.isfile(item["key"]):
            raise media.MediaError("fichier introuvable")
        tmp = tempfile.mkdtemp(dir=self.base, prefix=".tmp-")
        job.tmp = tmp
        try:
            try:
                self._ffmpeg(item, tracks, trick, tmp, job)  # une seule lecture du fichier pour tout
            except media.MediaError as e:
                log.info("Passe combinée en échec (%s), reprise séparée : %s", e, item["key"])
                if trick:
                    try:
                        self._ffmpeg(item, [], True, tmp, job)
                    except media.MediaError as e2:
                        log.warning("Vignettes de %s : %s", item["key"], e2)
                for i in tracks:
                    try:
                        self._ffmpeg(item, [i], False, tmp, job, fonts=False)
                    except media.MediaError as e2:
                        log.warning("Piste %s de %s : %s", i, item["key"], e2)
            if tracks:
                os.makedirs(self._dir(item), exist_ok=True)
            still = []
            pgs = set(pgs_tracks(_probe(item)))
            for i in tracks:
                if i in pgs:
                    sup = os.path.join(tmp, f"{i}.sup")
                    if os.path.isfile(sup) and os.path.getsize(sup) > 0:
                        os.replace(sup, self.sup_path(item, i))
                    else:
                        still.append(i)
                        with open(self.path(item, i), "w", encoding="utf-8") as f:
                            json.dump({"cues": [], "error": "piste illisible"}, f)
                    continue
                src = os.path.join(tmp, f"{i}.srt")
                dest = self.path(item, i)
                if os.path.isfile(src):
                    with open(src, "rb") as f:
                        data = {"cues": parse_subtitles(f.read())}
                else:
                    # piste illisible : on le note pour ne pas relire tout le fichier à chaque lecture
                    still.append(i)
                    data = {"cues": [], "error": "piste illisible"}
                with open(dest + ".tmp", "wb") as f:
                    f.write(json.dumps(data, ensure_ascii=False).encode())
                os.replace(dest + ".tmp", dest)
                raw_ass = os.path.join(tmp, f"{i}.ass")
                if os.path.isfile(raw_ass) and os.path.getsize(raw_ass) > 0:
                    os.replace(raw_ass, self.ass_path(item, i))
            if self._want_fonts(item):
                self._store_fonts(item, tmp)
            if trick:
                self._store_trick(item, tmp)
            if still:
                raise media.MediaError(f"{len(still)} piste(s) de sous-titres illisible(s)")
        finally:
            job.tmp = None
            shutil.rmtree(tmp, ignore_errors=True)

    def _want_fonts(self, item):
        return bool(ass_tracks(_probe(item))) and self.fonts_index(item) is None

    def _attachments(self, item):
        probe = _probe(item)
        if "attachments" in probe:
            return probe["attachments"]
        try:  # médias scannés avant la prise en charge des polices
            return media.attachments(item["key"])
        except media.MediaError:
            return []

    def _store_fonts(self, item, tmp):
        dest = os.path.join(self._dir(item), "fonts")
        os.makedirs(dest, exist_ok=True)
        index = []
        for f in sorted(os.listdir(tmp)):
            if not f.startswith("font_"):
                continue
            src = os.path.join(tmp, f)
            try:
                with open(src, "rb") as fh:
                    names = font_names(fh.read(64 * 1024 * 1024))
            except OSError:
                continue
            if not names:
                continue
            os.replace(src, os.path.join(dest, f))
            index.append({"file": f, "names": sorted(names), "size": os.path.getsize(os.path.join(dest, f))})
        with open(os.path.join(self._dir(item), "fonts.json"), "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False)

    def _trick_size(self, item):
        probe = json.loads(item["probe"] or "{}") if isinstance(item["probe"], str) else item["probe"]
        v = (probe or {}).get("video") or {}
        w, h = v.get("width") or 16, v.get("height") or 9
        return TRICK_W, max(2, int(round(TRICK_W * h / w / 2)) * 2)

    def _store_trick(self, item, tmp):
        sheets = sorted(f for f in os.listdir(tmp) if f.startswith("t") and f.endswith(".jpg"))
        if not sheets:
            return
        dest = self.trick_dir(item)
        os.makedirs(dest, exist_ok=True)
        for n, f in enumerate(sheets, 1):
            os.replace(os.path.join(tmp, f), os.path.join(dest, f"{n}.jpg"))
        w, h = self._trick_size(item)
        dur = (item["duration_ms"] or 0) / 1000
        index = {"interval": TRICK_INTERVAL, "w": w, "h": h, "cols": TRICK_COLS, "rows": TRICK_ROWS,
                 "count": int(math.ceil(dur / TRICK_INTERVAL)) if dur else len(sheets) * TRICK_COLS * TRICK_ROWS,
                 "sheets": len(sheets)}
        with open(os.path.join(dest, "index.json"), "w", encoding="utf-8") as f:
            json.dump(index, f)

    def _ffmpeg(self, item, tracks, trick, tmp, job, fonts=True):
        cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y"]
        if trick:
            cmd += ["-skip_frame", "nokey"]  # seules les images clés sont décodées : très peu de calcul
        if job.kind in ("soon", "gentle") and self._streaming() and media.has_option("readrate"):
            # laisse de la bande passante au flux en cours
            cmd += ["-readrate", str(SOON_READRATE if job.kind == "soon" else GENTLE_READRATE)]
        if fonts and tracks and self._want_fonts(item):
            for a in self._attachments(item)[:200]:
                cmd += [f"-dump_attachment:{int(a['index'])}", os.path.join(tmp, f"font_{int(a['index'])}{a['ext']}")]
        cmd += [*media.LOCAL_ONLY, "-analyzeduration", "200M", "-probesize", "1G", "-i", item["key"]]
        probe = _probe(item)
        if not trick and tracks:
            # horloge : une piste audio recopiée vers nulle part (sans décodage) indique précisément
            # jusqu'où le fichier a été lu, même pendant un long passage sans sous-titres
            clock = "0:a:0" if probe.get("audio") else "0:v:0"
            cmd += ["-map", clock, "-c", "copy", "-f", "null", "-"]
        if trick:
            w, h = self._trick_size(item)
            cmd += ["-map", "0:v:0", "-an", "-sn",
                    "-vf", f"fps=1/{TRICK_INTERVAL},scale={w}:{h},tile={TRICK_COLS}x{TRICK_ROWS}",
                    "-q:v", "6", "-f", "image2", os.path.join(tmp, "t%04d.jpg")]
        ass = set(ass_tracks(_probe(item)))
        pgs = set(pgs_tracks(_probe(item)))
        for i in tracks:
            # -flush_packets : chaque réplique est écrite tout de suite (lecture progressive)
            if i in pgs:
                cmd += ["-map", f"0:{i}", "-c:s", "copy", "-flush_packets", "1", "-f", "sup",
                        os.path.join(tmp, f"{i}.sup")]
                continue
            cmd += ["-map", f"0:{i}", "-c:s", "srt", "-flush_packets", "1", "-f", "srt", os.path.join(tmp, f"{i}.srt")]
            if i in ass:  # le script ASS d'origine, pour un rendu fidèle (positions, polices, karaoké)
                cmd += ["-map", f"0:{i}", "-c:s", "copy", "-flush_packets", "1", "-f", "ass",
                        os.path.join(tmp, f"{i}.ass")]
        cmd += ["-progress", "pipe:1", "-nostats"]
        duration = (item["duration_ms"] or 0) * 1000  # en microsecondes
        nice = 0 if job.kind == "play" else 10
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             preexec_fn=lambda: os.nice(nice))
        err = []
        t = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
        t.start()
        checked = time.time()
        fonts_stored = False
        for line in p.stdout:
            if job.kind in ("idle", "gentle") and (job.aborted or time.time() - checked > 3):
                checked = time.time()
                if job.aborted or (job.kind == "idle" and self._streaming()):
                    p.kill()
                    p.wait()
                    t.join(5)
                    p.stdout.close()
                    p.stderr.close()
                    raise Aborted()
            if line.startswith(b"out_time_us=") or line.startswith(b"out_time_ms="):
                try:
                    us = int(line.split(b"=", 1)[1])
                except ValueError:
                    continue
                job.until_ms = max(job.until_ms, us // 1000 - PARTIAL_MARGIN_MS)
                if fonts and not fonts_stored and tracks and self._want_fonts(item):
                    # les polices jointes sont écrites dès l'ouverture : disponibles avant la fin de l'extraction
                    fonts_stored = True
                    try:
                        self._store_fonts(item, tmp)
                    except OSError as e:
                        log.debug("Polices de %s : %s", item.get("key"), e)
                if duration > 0:
                    job.progress = max(job.progress, min(0.99, us / duration))
        p.wait()
        t.join(5)
        p.stdout.close()
        p.stderr.close()
        if p.returncode != 0:
            msg = (err[0] if err else b"").decode("utf-8", "replace").strip().splitlines()
            raise media.MediaError(msg[-1] if msg else "échec de l'extraction")

    def stop(self):
        self.stopped.set()
        self.wake.set()
