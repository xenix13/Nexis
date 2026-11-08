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
import signal
import subprocess
import threading
import time
from collections import deque

from . import media
from .i18n import tr
from .transcoder import hw_input, media_args

log = logging.getLogger("seance.downloads")

KEEP = 48 * 3600
MAX_PER_USER = 40
SPARE_DISK = 2 * 1024 ** 3   # on garde toujours 2 Go libres sur le disque du serveur
CPUS = os.cpu_count() or 2
COPY_READRATE = 20          # priorité discrète : lecture du partage plafonnée à 20x le temps réel
IONICE = shutil.which("ionice")
# Priorité des préparations (Paramètres → Visionnage hors ligne). Une conversion coûte ~30 s de processeur par
# minute de vidéo en 1080p, une copie ~2 s.
#  - discrète : le moins de processeur et de disque possible, en pause dès que quelqu'un regarde (ancien réglage) ;
#  - normale : pleine vitesse quand personne ne regarde ; pendant une lecture, priorité basse, et une conversion
#    ne s'arrête que si une autre conversion (celle d'un spectateur) a besoin du processeur ;
#  - rapide : tout le processeur, jamais en pause.
PRIORITIES = {
    "low": {"threads": max(1, min(4, CPUS // 4)), "nice": 19, "ionice": ["-c", "3"], "readrate": True, "workers": 1},
    "normal": {"threads": max(2, min(8, CPUS // 2)), "nice": 10, "ionice": ["-c", "2", "-n", "7"], "readrate": False,
               "workers": 2 if CPUS >= 8 else 1},
    "high": {"threads": max(2, min(16, CPUS)), "nice": 0, "ionice": [], "readrate": False, "workers": 2},
}


class DownloadError(Exception):
    pass


class Job:
    def __init__(self, uid, item_id, plan, path, duration, progressive=False):
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
        self.waiting = False      # en pause : quelqu'un regarde, il passe en premier
        # transfert pendant la préparation : MP4 fragmenté, écrit dans l'ordre ; l'appareil récupère ce qui est déjà
        # écrit (ready octets). gen change à chaque nouvel essai (repli de la carte graphique sur le processeur)
        self.progressive = bool(progressive) and self.mode != "original"
        self.ready = self.size
        self.gen = 0
        self.part = None

    def view(self):
        return {"id": self.id, "item_id": str(self.item_id), "state": self.state, "mode": self.mode,
                "progress": round(self.progress, 3), "size": self.size, "error": tr(self.error), "waiting": self.waiting,
                "progressive": self.progressive, "ready": self.ready, "gen": self.gen}


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
        self.workers = []

    # ------------------------------------------------------------ demandes
    def request(self, uid, row, plan, duration, progressive=False):
        with self.lock:
            mine = [j for j in self.jobs.values() if j.uid == uid and j.state in ("queued", "preparing")]
            if len(mine) >= MAX_PER_USER:
                raise DownloadError(f"Déjà {MAX_PER_USER} téléchargements en préparation : réessayez une fois ceux-ci prêts")
            job = Job(uid, row["id"], plan, row["key"], duration, progressive)
            self.jobs[job.id] = job
            if job.state == "queued":
                self.queue.append(job.id)
                # préparations simultanées : selon la priorité choisie et le nombre de cœurs
                self.workers = [w for w in self.workers if w.is_alive()]
                while len(self.workers) < self.settings()["workers"]:
                    w = threading.Thread(target=self._loop, daemon=True, name="downloads")
                    self.workers.append(w)
                    w.start()
                self.wake.set()
            return job

    def priority(self):
        try:
            v = self.ctx.db.setting("downloads_priority", "normal")
        finally:
            self.ctx.db.release()
        return v if v in PRIORITIES else "normal"

    def settings(self):
        return PRIORITIES[self.priority()]

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
            while True:
                with self.lock:
                    jid = self.queue.popleft() if self.queue else None
                    job = self.jobs.get(jid) if jid else None
                if not jid:
                    self.wake.clear()
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
        hw = self.ctx.hw() if job.plan["mode"] == "transcode" else None
        for attempt_hw in ([hw, None] if hw else [None]):  # carte graphique, sinon repli sur le processeur
            if job.state == "cancelled":
                return
            job.state, job.progress, job.ready = "preparing", 0.0, 0
            job.gen += 1  # l'appareil repart de zéro s'il avait commencé à récupérer l'essai précédent
            if self._run(job, out_dir, attempt_hw):
                return
        if job.state != "cancelled":
            job.state = "error"

    def _run(self, job, out_dir, hw):
        part, final = os.path.join(out_dir, "video.part.mp4"), os.path.join(out_dir, "video.mp4")
        copy = job.plan["mode"] == "copy"
        prio = self.priority()
        conf = PRIORITIES[prio]
        threads = str(conf["threads"])
        cmd = [*([IONICE, *conf["ionice"]] if IONICE and conf["ionice"] else []),  # part du disque laissée aux autres
               media.FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *(hw_input(hw) if hw else []),
               "-threads", threads,
               *(["-readrate", str(COPY_READRATE)] if copy and conf["readrate"] and media.readrate_ok() else []),
               *media.input_args(job.source), "-map_metadata", "-1", "-map_chapters", "-1",
               "-threads", threads, "-filter_threads", "1",
               *media_args(job.plan, hw), "-sn", "-dn", "-max_muxing_queue_size", "2048", "-nostats"]
        progress_r = progress_w = None
        if job.progressive:
            # MP4 fragmenté écrit d'un trait sur la sortie standard : rien n'est réécrit après coup, l'appareil peut
            # récupérer chaque partie dès qu'elle est écrite ; l'avancement passe par un autre tube
            progress_r, progress_w = os.pipe()
            cmd += ["-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-progress", f"pipe:{progress_w}",
                    "-f", "mp4", "pipe:1"]
        else:
            cmd += ["-movflags", "+faststart", "-progress", "pipe:1", "-f", "mp4", part]
        log.info("Préparation du téléchargement %s (%s%s, priorité %s)", job.id, job.mode, f", {hw['engine']}" if hw else "", prio)
        log_path = os.path.join(out_dir, "ffmpeg.log")
        try:
            with open(log_path, "wb") as log_file:
                nice = conf["nice"]
                job.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log_file,
                                            preexec_fn=(lambda: os.nice(nice)) if nice else None,
                                            pass_fds=(progress_w,) if job.progressive else ())
        except OSError as e:
            job.error = f"ffmpeg introuvable ({e})"
            if progress_r is not None:
                os.close(progress_r)
            return False
        finally:
            if progress_w is not None:
                os.close(progress_w)  # ffmpeg garde sa copie
        threading.Thread(target=self._yield_to_viewers, args=(job, job.proc, prio, conf["nice"]), daemon=True).start()
        def read_progress(stream):
            for line in stream:
                if line.startswith(b"out_time_us=") and job.duration:
                    try:
                        job.progress = min(0.99, int(line[12:]) / 1e6 / job.duration)
                    except ValueError:
                        pass
        if job.progressive:
            job.part = part
            def follow():
                with os.fdopen(progress_r, "rb") as stream:
                    read_progress(stream)
            reader = threading.Thread(target=follow, daemon=True)
            reader.start()
            with job.proc.stdout, open(part, "wb") as out:
                while True:
                    block = job.proc.stdout.read(1 << 20)
                    if not block:
                        break
                    out.write(block)
                    out.flush()
                    job.ready += len(block)  # écrit et vidé : lisible par l'appareil
            reader.join(5)
        else:
            with job.proc.stdout:
                read_progress(job.proc.stdout)
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                err = f.read().strip().splitlines()
        except OSError:
            err = []
        job.waiting = False
        if job.proc.wait() != 0 or not os.path.isfile(part):
            job.error = "Préparation impossible : " + (err[-1] if err else "ffmpeg s'est arrêté")
            log.warning("Téléchargement %s : %s", job.id, job.error)
            return False
        os.replace(part, final)
        job.path, job.size, job.progress, job.state, job.error = final, os.path.getsize(final), 1.0, "ready", None
        job.ready = job.size
        return True

    def _yield_to_viewers(self, job, proc, prio, nice):
        """Laisse la place aux spectateurs : selon la priorité, pause ou priorité basse pendant une lecture."""
        lowered = False
        while proc.poll() is None:
            if prio == "high":
                return
            viewing = self.ctx.viewing()
            if prio == "low":
                pause = viewing  # discrète : en pause dès que quelqu'un regarde
            else:
                # normale : une conversion s'efface devant celle d'un spectateur ; une copie continue, ralentie
                pause = job.mode == "transcode" and viewing and self.ctx.transcoder.counts()[1] > 0
                if viewing != lowered:
                    try:
                        os.setpriority(os.PRIO_PROCESS, proc.pid, 19 if viewing else nice)
                        lowered = viewing
                    except (OSError, PermissionError):
                        pass  # remonter la priorité demande des droits : elle reste basse jusqu'à la fin
            if pause != job.waiting:
                try:
                    os.kill(proc.pid, signal.SIGSTOP if pause else signal.SIGCONT)
                    job.waiting = pause
                except OSError:
                    return
            time.sleep(3)
