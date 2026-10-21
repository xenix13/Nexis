"""Préparation des sous-titres internes à l'avance.

Extraire une piste de sous-titres d'un MKV oblige ffmpeg à lire tout le fichier : sur un
partage réseau, cela peut prendre une à plusieurs minutes. On extrait donc toutes les pistes
texte d'un fichier en UNE seule lecture, et le plus tôt possible :
- en priorité quand quelqu'un lance la lecture (le lecteur attend, avec une progression) ;
- en urgence quand on ouvre une fiche ou que l'épisode suivant approche ;
- en tâche de fond pour les nouveaux fichiers, uniquement quand aucune lecture n'est en cours.
"""
import collections
import json
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


def text_tracks(probe):
    return [s["index"] for s in (probe or {}).get("subs", []) if s.get("codec") in media.TEXT_SUB_CODECS]


class SubtitleCache:
    def __init__(self, ctx):
        self.ctx = ctx
        self.base = os.path.join(ctx.data_dir, "cache", "subs")
        os.makedirs(self.base, exist_ok=True)
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
        if not self.missing(item):
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
                if not row or not self.missing(row):
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
        if not tracks:
            return
        if not item["key"] or not os.path.isfile(item["key"]):
            raise media.MediaError("fichier introuvable")
        out_dir = self._dir(item)
        os.makedirs(out_dir, exist_ok=True)
        tmp = tempfile.mkdtemp(dir=self.base, prefix=".tmp-")
        try:
            try:
                self._ffmpeg(item, tracks, tmp, job)
            except media.MediaError:
                # une piste problématique fait échouer toute la passe : on reprend piste par piste
                for i in tracks:
                    try:
                        self._ffmpeg(item, [i], tmp, job)
                    except media.MediaError as e:
                        log.warning("Piste %s de %s : %s", i, item["key"], e)
            for i in tracks:
                src = os.path.join(tmp, f"{i}.srt")
                if not os.path.isfile(src):
                    continue
                with open(src, "rb") as f:
                    cues = parse_subtitles(f.read())
                data = json.dumps({"cues": cues}, ensure_ascii=False).encode()
                dest = self.path(item, i)
                with open(dest + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(dest + ".tmp", dest)
            still = [i for i in tracks if not os.path.isfile(self.path(item, i))]
            if still:
                raise media.MediaError(f"{len(still)} piste(s) illisible(s)")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _ffmpeg(self, item, tracks, tmp, job):
        cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", item["key"]]
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
