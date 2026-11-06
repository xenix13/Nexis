"""Sous-titres générés à partir de la voix (Whisper), pour les vidéos qui n'en ont pas.

Trois façons de faire, choisies d'elles-mêmes dans cet ordre :
- un service de transcription compatible avec l'API d'OpenAI (/v1/audio/transcriptions), indiqué par
  l'administrateur : par exemple Speaches (faster-whisper) dans un conteneur à côté de Nexis, ou OpenAI
  lui-même (l'audio quitte alors la maison) ;
- le filtre « whisper » d'ffmpeg 8 (compilé avec whisper.cpp) ;
- le programme whisper-cli de whisper.cpp.
Pour les deux derniers, Nexis télécharge le modèle choisi (fichier ggml de whisper.cpp) dans data/whisper.

La transcription est dans la langue parlée (celle de la piste audio) : Whisper ne traduit que vers l'anglais.
C'est long : de quelques minutes à plus d'une heure de calcul pour un film selon le modèle et le processeur ;
une seule à la fois, mise en pause pendant les lectures.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import media
from .opensubs import TO_OS, attach
from .subtitles import parse_subtitles

log = logging.getLogger("seance.whisper")

MODELS = {"tiny": 75, "base": 142, "small": 466, "medium": 1500}  # Mo
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{}.bin"
CHUNK = 600            # service distant : morceaux de 10 min (taille de fichier limitée par les API)
USER_AGENT = "Nexis"


class WhisperError(Exception):
    pass


def srt_time(ms):
    ms = max(0, int(ms))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(cues):
    return "".join(f"{n}\n{srt_time(c['s'])} --> {srt_time(c['e'])}\n{c['t']}\n\n" for n, c in enumerate(cues, 1)).encode()


def segments_to_cues(data, offset_ms=0):
    """Réponse « verbose_json » (segments start/end en secondes) -> repères."""
    out = []
    for seg in (data or {}).get("segments") or []:
        try:
            s, e, t = float(seg["start"]), float(seg["end"]), str(seg.get("text") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        if t:
            out.append({"s": offset_ms + int(s * 1000), "e": offset_ms + int(e * 1000), "t": t})
    return out


class Whisper:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lock = threading.Lock()
        self.queue = []          # identifiants des titres demandés à la main
        self.current = None      # {"id", "title", "progress", "started"}
        self.wake = threading.Event()
        self.model_job = None    # téléchargement d'un modèle en cours {"model", "done", "total", "error"}
        self.thread = None

    # ------------------------------------------------------------ réglages
    def conf(self):
        db = self.ctx.db
        return {"url": db.setting("whisper_url") or "", "key": db.setting("whisper_key") or "",
                "model_http": db.setting("whisper_model_http") or "whisper-1",
                "model": db.setting("whisper_model") or "base",
                "threads": self.ctx.setting_int("whisper_threads", max(1, (os.cpu_count() or 2) // 2))}

    def model_path(self, name=None):
        name = name or self.conf()["model"]
        return os.path.join(self.ctx.data_dir, "whisper", f"ggml-{name}.bin")

    def tools(self):
        return {"filter": media.has_filter("whisper"), "cli": shutil.which("whisper-cli") or shutil.which("whisper-cpp")}

    def backend(self):
        c = self.conf()
        if c["url"]:
            return "http"
        if os.path.isfile(self.model_path()):
            t = self.tools()
            if t["filter"]:
                return "filter"
            if t["cli"]:
                return "cli"
        return None

    def available(self):
        b = self.backend()
        if b:
            return True, None
        t = self.tools()
        if not (t["filter"] or t["cli"]):
            return False, "Indiquez un service de transcription (Paramètres → Sous-titres et son → Whisper)"
        return False, "Téléchargez un modèle Whisper (Paramètres → Sous-titres et son → Whisper)"

    # ------------------------------------------------------------ modèles (whisper.cpp)
    def download_model(self, name):
        if name not in MODELS:
            raise WhisperError("Modèle inconnu")
        with self.lock:
            if self.model_job and not self.model_job.get("finished"):
                raise WhisperError("Un modèle est déjà en cours de téléchargement")
            self.model_job = {"model": name, "done": 0, "total": MODELS[name] * 1024 * 1024, "error": None, "finished": False}
        threading.Thread(target=self._download, args=(name,), daemon=True, name="whisper-model").start()

    def _download(self, name):
        job = self.model_job
        dest = self.model_path(name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        part = dest + ".partial"
        try:
            req = urllib.request.Request(MODEL_URL.format(name), headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                job["total"] = int(r.headers.get("Content-Length") or job["total"])
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    job["done"] += len(chunk)
            if job["done"] < 10 * 1024 * 1024:
                raise WhisperError("fichier incomplet")
            os.replace(part, dest)
            self.ctx.db.set_setting("whisper_model", name)
        except (OSError, urllib.error.URLError, WhisperError) as e:
            job["error"] = str(getattr(e, "reason", e))
            try:
                os.remove(part)
            except OSError:
                pass
        finally:
            job["finished"] = True
            self.ctx.db.release()

    # ------------------------------------------------------------ choix de la piste
    @staticmethod
    def audio_track(probe):
        audios = probe.get("audio") or []
        return next((a for a in audios if a.get("default")), audios[0] if audios else None)

    @staticmethod
    def needs(probe):
        """Langue à transcrire si le titre n'a aucun sous-titre complet dans la langue parlée, sinon None."""
        a = Whisper.audio_track(probe)
        if not a:
            return None
        lang = a.get("lang") or ""
        have = {s.get("lang") for s in probe.get("subs", []) if not s.get("forced")}
        have |= {e.get("lang") for e in probe.get("external", []) if not e.get("forced")}
        if lang and lang in have:
            return None
        if not lang and (probe.get("subs") or probe.get("external")):
            return None  # langue parlée inconnue, mais déjà des sous-titres : on ne devine pas
        return lang or "und"

    # ------------------------------------------------------------ génération
    def generate(self, row, progress=lambda p: None, check=lambda: None):
        """Transcrit la piste audio principale d'un film ou d'un épisode -> entrée de sous-titres ajoutée."""
        if not row.get("key") or not os.path.isfile(row["key"]):
            raise WhisperError("Fichier introuvable")
        probe = json.loads(row["probe"] or "{}")
        a = self.audio_track(probe)
        if not a:
            raise WhisperError("Pas de piste audio")
        lang = a.get("lang") or "und"
        iso1 = TO_OS.get(lang, "").split("-")[0] if lang != "und" else ""
        duration = probe.get("duration") or (row.get("duration_ms") or 0) / 1000
        backend = self.backend()
        if not backend:
            raise WhisperError(self.available()[1])
        tmp = tempfile.mkdtemp(prefix="whisper-", dir=self.ctx.data_dir)
        try:
            if backend == "http":
                cues = self._http(row["key"], a["index"], iso1, duration, tmp, progress, check)
            elif backend == "filter":
                cues = self._filter(row["key"], a["index"], iso1, duration, tmp, progress, check)
            else:
                cues = self._cli(row["key"], a["index"], iso1, tmp, progress, check)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if not cues:
            raise WhisperError("Aucune parole reconnue")
        model = self.conf()["model_http"] if backend == "http" else self.conf()["model"]
        return attach(self.ctx, row, f"{lang}-whisper.srt", to_srt(cues), lang if lang != "und" else "",
                      {"dl": "whisper", "release": f"Whisper ({model})"})

    def _run(self, cmd, check, on_line=None, timeout=6 * 3600):
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        started, tail = time.time(), []
        try:
            for raw in p.stdout:
                line = raw.decode("utf-8", "replace").strip()
                tail = (tail + [line])[-5:]
                if on_line:
                    on_line(line)
                check()
                if time.time() - started > timeout:
                    raise WhisperError("délai dépassé")
            if p.wait() != 0:
                raise WhisperError(tail[-1] if tail else "échec")
        except BaseException:
            p.kill()
            p.wait()
            raise
        finally:
            p.stdout.close()

    def _filter(self, path, index, iso1, duration, tmp, progress, check):
        out = os.path.join(tmp, "out.srt")
        esc = lambda v: v.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")
        af = (f"whisper=model={esc(self.model_path())}:language={iso1 or 'auto'}:queue=15:"
              f"destination={esc(out)}:format=srt")

        def line(l):
            m = re.match(r"^out_time_us=(\d+)", l)
            if m and duration:
                progress(min(0.99, int(m.group(1)) / 1e6 / duration))
        self._run([media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-progress", "pipe:1", *media.input_args(path),
                   "-map", f"0:{index}", "-vn", "-af", af, "-threads", str(self.conf()["threads"]), "-f", "null", "-"], check, line)
        with open(out, "rb") as f:
            return parse_subtitles(f.read())

    def _cli(self, path, index, iso1, tmp, progress, check):
        wav = os.path.join(tmp, "audio.wav")
        self._run([media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", *media.input_args(path), "-map", f"0:{index}",
                   "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav], check)
        progress(0.05)
        base = os.path.join(tmp, "out")

        def line(l):
            m = re.search(r"progress\s*=\s*(\d+)%", l)
            if m:
                progress(0.05 + 0.94 * int(m.group(1)) / 100)
        self._run([self.tools()["cli"], "-m", self.model_path(), "-f", wav, "-l", iso1 or "auto", "-osrt", "-of", base,
                   "-t", str(self.conf()["threads"]), "-pp"], check, line)
        with open(base + ".srt", "rb") as f:
            return parse_subtitles(f.read())

    def _http(self, path, index, iso1, duration, tmp, progress, check):
        c = self.conf()
        url = c["url"].rstrip("/")
        if not url.endswith("/audio/transcriptions"):
            url += "/v1/audio/transcriptions" if not url.endswith("/v1") else "/audio/transcriptions"
        cues = []
        parts = max(1, int((duration or CHUNK) // CHUNK) + (1 if (duration or 0) % CHUNK else 0))
        for n in range(parts):
            check()
            start = n * CHUNK
            chunk = os.path.join(tmp, f"part{n}.mp3")
            media.run([media.FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-y", "-ss", str(start), "-t", str(CHUNK),
                       *media.input_args(path), "-map", f"0:{index}", "-vn", "-ac", "1", "-ar", "16000",
                       "-c:a", "libmp3lame", "-b:a", "48k", chunk], timeout=600)
            if os.path.getsize(chunk) < 2048:
                continue  # fin du fichier (durée annoncée approximative)
            with open(chunk, "rb") as f:
                audio = f.read()
            cues += self._post(url, c, audio, iso1, start * 1000)
            progress((n + 1) / parts)
        return cues

    def _post(self, url, c, audio, iso1, offset_ms):
        def send(fmt):
            boundary = uuid.uuid4().hex
            fields = {"model": c["model_http"], "response_format": fmt}
            if iso1:
                fields["language"] = iso1
            body = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                            for k, v in fields.items())
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.mp3\"\r\n"
                     "Content-Type: audio/mpeg\r\n\r\n").encode() + audio + f"\r\n--{boundary}--\r\n".encode()
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": USER_AGENT}
            if c["key"]:
                headers["Authorization"] = f"Bearer {c['key']}"
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=900) as r:
                return r.read(20 * 1024 * 1024)
        try:
            raw = send("srt")
            cues = parse_subtitles(raw) if b"-->" in raw[:4096] else segments_to_cues(json.loads(raw or b"{}"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise WhisperError("Le service de transcription refuse la clé d'API")
            if e.code not in (400, 422):
                raise WhisperError(f"Service de transcription : erreur {e.code}")
            try:  # format srt non proposé : segments en JSON
                cues = segments_to_cues(json.loads(send("verbose_json") or b"{}"))
            except (urllib.error.URLError, OSError, ValueError) as e2:
                raise WhisperError(f"Service de transcription : {getattr(e2, 'reason', e2)}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise WhisperError(f"Service de transcription injoignable : {getattr(e, 'reason', e)}")
        if offset_ms:  # repères relatifs au morceau
            for cue in cues:
                cue["s"] += offset_ms
                cue["e"] += offset_ms
        return cues

    # ------------------------------------------------------------ demandes faites à la main
    def enqueue(self, item_id):
        with self.lock:
            if item_id not in self.queue and not (self.current and self.current["id"] == item_id):
                self.queue.append(item_id)
        self.wake.set()
        if not self.thread:
            self.thread = threading.Thread(target=self._worker, daemon=True, name="whisper")
            self.thread.start()

    def status(self):
        with self.lock:
            return {"queue": list(self.queue), "current": dict(self.current) if self.current else None}

    def _worker(self):
        while True:
            self.wake.wait(60)
            self.wake.clear()
            while True:
                with self.lock:
                    if not self.queue:
                        break
                    iid = self.queue.pop(0)
                db = self.ctx.db
                row = db.one("SELECT * FROM items WHERE id=?", (iid,))
                db.release()
                if not row:
                    continue
                self.current = {"id": iid, "title": row["title"], "progress": 0.0, "started": int(time.time())}
                try:
                    self.generate(row, progress=lambda p: self.current.update(progress=round(p, 3)), check=self._pause)
                except Exception as e:
                    log.warning("Whisper, %s : %s", row["title"], e)
                    self.current.update(error=str(e))
                    try:
                        self.ctx.health.raise_alert(f"whisper:{iid}", "task", "Sous-titres Whisper impossibles",
                                                    f"{row['title']} : {e}", f"#/item/{iid}")
                    except Exception:
                        pass
                finally:
                    self.ctx.db.release()
                    with self.lock:
                        self.current = None

    def _pause(self):
        """Pendant une lecture, le calcul attend (le processeur et le partage réseau servent d'abord à regarder)."""
        if self.backend() == "http":
            return
        while self.ctx.subtitles._streaming():
            time.sleep(10)


# ------------------------------------------------------------------ routes
def register(app, ctx, x):
    from .catalog import RK
    from .web import HTTPError
    db = ctx.db
    w = ctx.whisper

    def view():
        c = w.conf()
        t = w.tools()
        models = {m: {"size_mb": s, "installed": os.path.isfile(w.model_path(m))} for m, s in MODELS.items()}
        ok, note = w.available()
        return {"url": c["url"], "key_set": bool(c["key"]), "model_http": c["model_http"], "model": c["model"],
                "threads": c["threads"], "cpus": os.cpu_count(), "backend": w.backend(), "available": ok, "note": note,
                "filter": t["filter"], "cli": bool(t["cli"]), "models": models,
                "download": dict(w.model_job) if w.model_job else None, **w.status()}

    @app.route("GET", "/api/admin/whisper", auth="admin")
    def admin_get(req):
        return view()

    @app.route("PUT", "/api/admin/whisper", auth="admin")
    def admin_put(req):
        b = req.json()
        if "url" in b:
            url = str(b["url"] or "").strip()
            u = urllib.parse.urlparse(url)
            if url and (u.scheme not in ("http", "https") or not u.netloc or len(url) > 300):
                raise HTTPError(400, "Adresse du service invalide (http://… ou https://…)")
            db.set_setting("whisper_url", url or None)
        if b.get("key"):
            key = str(b["key"]).strip()
            if len(key) > 300 or not re.match(r"^[\x21-\x7e]+$", key):
                raise HTTPError(400, "Clé d'API invalide")
            db.set_setting("whisper_key", key)
        if b.get("clear_key"):
            db.set_setting("whisper_key", None)
        if "model_http" in b:
            m = str(b["model_http"] or "").strip()
            if not re.match(r"^[A-Za-z0-9._/:-]{1,120}$", m):
                raise HTTPError(400, "Nom de modèle invalide")
            db.set_setting("whisper_model_http", m)
        if "model" in b:
            if b["model"] not in MODELS:
                raise HTTPError(400, "Modèle inconnu")
            db.set_setting("whisper_model", b["model"])
        if "threads" in b:
            n = b["threads"]
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 64:
                raise HTTPError(400, "Nombre de cœurs invalide")
            db.set_setting("whisper_threads", n)
        return view()

    @app.route("POST", "/api/admin/whisper/model", auth="admin")
    def admin_model(req):
        try:
            w.download_model(req.json().get("model"))
        except WhisperError as e:
            raise HTTPError(400, str(e))
        return view()

    @app.route("POST", f"/api/items/{RK}/whisper", auth="admin")
    def generate(req):
        r = x.require_item(req.user, req.params["rk"])
        if r["type"] not in ("movie", "episode") or r.get("peer_id"):
            raise HTTPError(400, "Sous-titres : film ou épisode de ce serveur seulement")
        ok, note = w.available()
        if not ok:
            raise HTTPError(400, note)
        w.enqueue(r["id"])
        return {"ok": True, **w.status()}

    @app.route("DELETE", f"/api/items/{RK}/whisper", auth="admin")
    def remove(req):
        r = x.require_item(req.user, req.params["rk"])
        probe = json.loads(r["probe"] or "{}")
        e = next((e for e in probe.get("external", []) if e.get("dl") == "whisper"), None)
        if not e or not ctx.opensubs.remove(r, e["path"], mark=f"w:{e.get('lang') or ''}"):
            raise HTTPError(404, "Sous-titre introuvable")
        return {"ok": True}
