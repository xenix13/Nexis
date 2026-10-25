"""Téléchargements pour regarder hors ligne (comme Netflix).

Nexis prépare un MP4 que l'appareil sait lire, à la qualité choisie, avec la piste audio voulue (et les
sous-titres image incrustés si on les demande). L'application le récupère ensuite par morceaux et le garde
dans le stockage du navigateur. Un MP4 déjà lisible tel quel est envoyé directement, sans préparation.

Les fichiers préparés sont temporaires : effacés dès que l'appareil a fini de les récupérer, sinon au bout de
48 h. shortcut: la file d'attente vit en mémoire ; après un redémarrage, l'application redemande la
préparation (elle reprend toute seule).
"""
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
from collections import deque

from . import media
from .transcoder import hw_input, media_args

log = logging.getLogger("seance.downloads")

KEEP = 48 * 3600
MAX_PER_USER = 40
SPARE_DISK = 2 * 1024 ** 3   # on garde toujours 2 Go libres sur le disque du serveur


class DownloadError(Exception):
    pass


class Job:
    def __init__(self, uid, item_id, plan, path, duration):
        self.id = secrets.token_urlsafe(9)
        self.uid = uid
        self.item_id = item_id
        self.plan = plan
        self.source = path
        self.duration = duration or 0     # secondes
        self.mode = "original" if plan["mode"] == "direct" else plan["mode"]
        self.state = "ready" if self.mode == "original" else "queued"
        self.progress = 1.0 if self.mode == "original" else 0.0
        self.path = path if self.mode == "original" else None
        self.size = os.path.getsize(path) if self.mode == "original" else 0
        self.error = None
        self.created = self.touched = time.time()
        self.proc = None

    def view(self):
        return {"id": self.id, "item_id": str(self.item_id), "state": self.state, "mode": self.mode,
                "progress": round(self.progress, 3), "size": self.size, "error": self.error}


class Downloads:
    def __init__(self, ctx):
        self.ctx = ctx
        self.base = os.path.join(ctx.data_dir, "downloads")
        shutil.rmtree(self.base, ignore_errors=True)  # préparations d'avant le redémarrage : redemandées
        os.makedirs(self.base, exist_ok=True)
        self.jobs = {}
        self.queue = deque()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.worker = None

    # ------------------------------------------------------------ demandes
    def request(self, uid, row, plan, duration):
        with self.lock:
            mine = [j for j in self.jobs.values() if j.uid == uid and j.state in ("queued", "preparing")]
            if len(mine) >= MAX_PER_USER:
                raise DownloadError(f"Déjà {MAX_PER_USER} téléchargements en préparation : attends qu'ils avancent")
            job = Job(uid, row["id"], plan, row["key"], duration)
            self.jobs[job.id] = job
            if job.state == "queued":
                self.queue.append(job.id)
                if not self.worker or not self.worker.is_alive():
                    self.worker = threading.Thread(target=self._loop, daemon=True, name="downloads")
                    self.worker.start()
                self.wake.set()
            return job

    def get(self, jid, uid):
        with self.lock:
            job = self.jobs.get(jid)
        if not job or job.uid != uid:
            return None
        job.touched = time.time()
        return job

    def delete(self, jid, uid):
        with self.lock:
            job = self.jobs.get(jid)
            if not job or job.uid != uid:
                return False
            self.jobs.pop(jid, None)
        self._drop(job)
        return True

    def _drop(self, job):
        job.state = "cancelled"
        if job.proc and job.proc.poll() is None:
            job.proc.kill()
        if job.mode != "original":
            shutil.rmtree(os.path.join(self.base, job.id), ignore_errors=True)

    def reap(self):
        now = time.time()
        with self.lock:
            old = [j for j in self.jobs.values() if now - j.touched > KEEP]
            for j in old:
                self.jobs.pop(j.id, None)
        for j in old:
            self._drop(j)

    # ------------------------------------------------------------ préparation
    def _loop(self):
        while True:
            self.wake.wait(30)
            self.wake.clear()
            while True:
                with self.lock:
                    jid = self.queue.popleft() if self.queue else None
                    job = self.jobs.get(jid) if jid else None
                if not jid:
                    break
                if job and job.state == "queued":
                    self._prepare(job)

    def _estimate(self, job):
        p = job.plan
        if p["mode"] == "copy":
            return os.path.getsize(job.source) if not media.is_strm(job.source) else 0
        return int((p["kbps"] + 192) * 1000 / 8 * job.duration)

    def _prepare(self, job):
        out_dir = os.path.join(self.base, job.id)
        os.makedirs(out_dir, exist_ok=True)
        if shutil.disk_usage(self.base).free < self._estimate(job) + SPARE_DISK:
            job.state, job.error = "error", "Espace disque insuffisant sur le serveur pour préparer ce téléchargement"
            return
        if job.plan["mode"] == "transcode":
            # priorité à ceux qui regardent : on attend qu'un transcodage se libère
            while self.ctx.transcoder.counts()[1] >= max(1, self.ctx.limits()[1]) and job.state != "cancelled":
                time.sleep(10)
        hw = self.ctx.hw() if job.plan["mode"] == "transcode" else None
        for attempt_hw in ([hw, None] if hw else [None]):  # carte graphique, sinon repli sur le processeur
            if job.state == "cancelled":
                return
            job.state, job.progress = "preparing", 0.0
            if self._run(job, out_dir, attempt_hw):
                return
        if job.state != "cancelled":
            job.state = "error"

    def _run(self, job, out_dir, hw):
        part, final = os.path.join(out_dir, "video.part.mp4"), os.path.join(out_dir, "video.mp4")
        cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *(hw_input(hw) if hw else []),
               *media.input_args(job.source), "-map_metadata", "-1", "-map_chapters", "-1",
               *media_args(job.plan, hw), "-sn", "-dn", "-movflags", "+faststart", "-max_muxing_queue_size", "2048",
               "-progress", "pipe:1", "-nostats", "-f", "mp4", part]
        log.info("Préparation du téléchargement %s (%s%s)", job.id, job.mode, f", {hw['engine']}" if hw else "")
        log_path = os.path.join(out_dir, "ffmpeg.log")
        try:
            with open(log_path, "wb") as log_file:
                job.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log_file,
                                            preexec_fn=lambda: os.nice(10))  # après les lectures en cours
        except OSError as e:
            job.error = f"ffmpeg introuvable ({e})"
            return False
        with job.proc.stdout:
            for line in job.proc.stdout:
                if line.startswith(b"out_time_us=") and job.duration:
                    try:
                        job.progress = min(0.99, int(line[12:]) / 1e6 / job.duration)
                    except ValueError:
                        pass
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                err = f.read().strip().splitlines()
        except OSError:
            err = []
        if job.proc.wait() != 0 or not os.path.isfile(part):
            job.error = "Préparation impossible : " + (err[-1] if err else "ffmpeg s'est arrêté")
            log.warning("Téléchargement %s : %s", job.id, job.error)
            return False
        os.replace(part, final)
        job.path, job.size, job.progress, job.state, job.error = final, os.path.getsize(final), 1.0, "ready", None
        return True
