"""Préparation à l'avance : sous-titres internes et vignettes d'aperçu (barre de lecture).

Extraire une piste de sous-titres d'un MKV oblige ffmpeg à lire tout le fichier : sur un
partage réseau, cela peut prendre une à plusieurs minutes. On extrait donc toutes les pistes
texte d'un fichier en UNE seule lecture, et le plus tôt possible :
- en priorité quand quelqu'un lance la lecture (le lecteur attend, avec une progression) ;
- en urgence quand on ouvre une fiche ou que l'épisode suivant approche ;
- en tâche de fond pour les nouveaux fichiers, uniquement quand aucune lecture n'est en cours.
"""
import collections
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
from .subtitles import parse_subtitles

log = logging.getLogger("seance.subs")


class Job:
    def __init__(self, item_id):
        self.item_id = item_id
        self.progress = 0.0
        self.done = threading.Event()
        self.error = None


TRICK_INTERVAL = 10   # une vignette toutes les 10 s
TRICK_W = 240
TRICK_COLS = TRICK_ROWS = 10


def text_tracks(probe):
    return [s["index"] for s in (probe or {}).get("subs", []) if s.get("codec") in media.TEXT_SUB_CODECS]


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
    def _dir(self, item):
        return os.path.join(self.base, f"{item['id']}_{item['file_mtime'] or 0}")

    def path(self, item, index):
        return os.path.join(self._dir(item), f"{int(index)}.json")

    def missing(self, item):
        probe = json.loads(item["probe"] or "{}") if isinstance(item["probe"], str) else item["probe"]
        return [i for i in text_tracks(probe) if not os.path.isfile(self.path(item, i))]

    # -- vignettes d'aperçu
    def trick_dir(self, item):
        return os.path.join(self.trick_base, f"{item['id']}_{item['file_mtime'] or 0}")

    def trick_index(self, item):
        try:
            with open(os.path.join(self.trick_dir(item), "index.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def needs_trick(self, item):
        if self.ctx.db.setting("trickplay", "1") != "1":
            return False
        probe = json.loads(item["probe"] or "{}") if isinstance(item["probe"], str) else item["probe"]
        if not (probe or {}).get("video") or (probe.get("duration") or 0) < 60:
            return False
        return self.trick_index(item) is None

    def has_work(self, item):
        return bool(self.missing(item)) or self.needs_trick(item)

    def read(self, item, index):
        try:
            with open(self.path(item, index), "rb") as f:
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
        if not self.has_work(item):
            return None
        with self.lock:
            job = self.jobs.get(item["id"])
            if job and not job.done.is_set():
                return job
            job = Job(item["id"])
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
                return
            if urgent:
                self.idle.pop(item_id, None)
                self.urgent[item_id] = True
                self.urgent.move_to_end(item_id)
            elif item_id not in self.urgent:
                self.idle[item_id] = True
        self.wake.set()

    def wait(self, job, timeout):
        return job.done.wait(timeout)

    # ------------------------------------------------------------ travail
    def _streaming(self):
        try:
            return bool(self.ctx.db.scalar("SELECT COUNT(*) FROM streams WHERE last_seen > ?", (int(time.time()) - 90,)))
        except Exception:
            return False

    def _next(self):
        with self.lock:
            if self.urgent:
                return self.urgent.popitem(last=False)[0]
        if self.idle and not self._streaming():
            with self.lock:
                if self.idle:
                    return self.idle.popitem(last=False)[0]
        return None

    def _worker(self):
        while not self.stopped.is_set():
            self.wake.wait(10)
            self.wake.clear()
            while not self.stopped.is_set():
                item_id = self._next()
                if item_id is None:
                    break
                try:
                    row = self.ctx.db.one("SELECT id, key, probe, file_mtime, duration_ms FROM items WHERE id=?", (item_id,))
                finally:
                    self.ctx.db.release()
                if not row or not self.has_work(row):
                    continue
                with self.lock:
                    if item_id in self.jobs and not self.jobs[item_id].done.is_set():
                        continue
                    job = Job(item_id)
                    self.jobs[item_id] = job
                self._run_slot(row, job)

    def _run_slot(self, item, job):
        with self.slots:
            try:
                self._extract(item, job)
            except Exception as e:
                log.warning("Sous-titres de %s : %s", item.get("key"), e)
                job.error = str(e)
            finally:
                job.progress = 1.0 if not job.error else job.progress
                job.done.set()
                with self.lock:
                    # on garde les jobs terminés en erreur un moment pour l'afficher, pas les autres
                    if not job.error:
                        self.jobs.pop(item["id"], None)

    def _extract(self, item, job):
        tracks = self.missing(item)
        trick = self.needs_trick(item)
        if not tracks and not trick:
            return
        if not item["key"] or not os.path.isfile(item["key"]):
            raise media.MediaError("fichier introuvable")
        tmp = tempfile.mkdtemp(dir=self.base, prefix=".tmp-")
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
                        self._ffmpeg(item, [i], False, tmp, job)
                    except media.MediaError as e2:
                        log.warning("Piste %s de %s : %s", i, item["key"], e2)
            if tracks:
                os.makedirs(self._dir(item), exist_ok=True)
            for i in tracks:
                src = os.path.join(tmp, f"{i}.srt")
                if not os.path.isfile(src):
                    continue
                with open(src, "rb") as f:
                    cues = parse_subtitles(f.read())
                dest = self.path(item, i)
                with open(dest + ".tmp", "wb") as f:
                    f.write(json.dumps({"cues": cues}, ensure_ascii=False).encode())
                os.replace(dest + ".tmp", dest)
            if trick:
                self._store_trick(item, tmp)
            still = [i for i in tracks if not os.path.isfile(self.path(item, i))]
            if still:
                raise media.MediaError(f"{len(still)} piste(s) de sous-titres illisible(s)")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

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

    def _ffmpeg(self, item, tracks, trick, tmp, job):
        cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y"]
        if trick:
            cmd += ["-skip_frame", "nokey"]  # seules les images clés sont décodées : très peu de calcul
        cmd += ["-i", item["key"]]
        if trick:
            w, h = self._trick_size(item)
            cmd += ["-map", "0:v:0", "-an", "-sn",
                    "-vf", f"fps=1/{TRICK_INTERVAL},scale={w}:{h},tile={TRICK_COLS}x{TRICK_ROWS}",
                    "-q:v", "6", "-f", "image2", os.path.join(tmp, "t%04d.jpg")]
        for i in tracks:
            cmd += ["-map", f"0:{i}", "-c:s", "srt", "-f", "srt", os.path.join(tmp, f"{i}.srt")]
        cmd += ["-progress", "pipe:1", "-nostats"]
        duration = (item["duration_ms"] or 0) * 1000  # en microsecondes
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                             preexec_fn=lambda: os.nice(10))
        err = []
        t = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
        t.start()
        for line in p.stdout:
            if line.startswith(b"out_time_us=") or line.startswith(b"out_time_ms="):
                try:
                    us = int(line.split(b"=", 1)[1])
                except ValueError:
                    continue
                if duration > 0:
                    job.progress = max(job.progress, min(0.99, us / duration))
        p.wait()
        t.join(5)
        if p.returncode != 0:
            msg = (err[0] if err else b"").decode("utf-8", "replace").strip().splitlines()
            raise media.MediaError(msg[-1] if msg else "échec de l'extraction")

    def stop(self):
        self.stopped.set()
        self.wake.set()
