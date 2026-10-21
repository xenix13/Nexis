"""Lecture : décision direct / remux / transcodage et sessions ffmpeg HLS.

Sans GPU, tout l'enjeu est de ne transcoder la vidéo que lorsque c'est indispensable :
- direct    : le navigateur lit le fichier tel quel (MP4 H.264 + AAC)            -> 0 % CPU
- copy      : vidéo H.264 recopiée dans des segments HLS, audio converti au besoin -> très peu de CPU
- transcode : HEVC, H.264 10 bits, sous-titres image incrustés, qualité réduite   -> libx264
"""
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time

from . import media

log = logging.getLogger("seance.transcode")

QUALITY = {"original": None, "1080p": (1080, 8000), "720p": (720, 4000), "480p": (480, 1500)}
BROWSER_AUDIO = {"aac", "mp3"}
# 5.1/7.1 -> stéréo sans écrêtage : rematrix normalisé, léger gain, puis limiteur
DOWNMIX = "aresample=rematrix_maxval=1.0,volume=4dB,alimiter=limit=0.8:level=disabled"
SEGMENT = 4  # secondes
SEG_RE = re.compile(r"^s(\d{5})\.ts$")
AHEAD_PAUSE = 45   # segments d'avance (~3 min) avant de mettre ffmpeg en pause
AHEAD_RESUME = 20
KEEP_BEHIND = 30


class Busy(Exception):
    pass


class StreamError(Exception):
    pass


def video_ok(v):
    if not v or v.get("codec") != "h264":
        return False
    prof = (v.get("profile") or "").lower()
    if "10" in prof or "4:2:2" in prof or "4:4:4" in prof:
        return False
    return (v.get("pix_fmt") or "yuv420p") in ("yuv420p", "yuvj420p")


def plan(probe, audio_index=None, burn_index=None, quality="original"):
    v = probe.get("video")
    audios = probe.get("audio") or []
    a = next((x for x in audios if x["index"] == audio_index), audios[0] if audios else None)
    target = QUALITY.get(quality)
    src_h = (v or {}).get("height") or 0
    rate_kbps = int((probe.get("bitrate") or 0) / 1000)
    need_scale = bool(target and src_h > target[0])
    over_rate = bool(target and rate_kbps and rate_kbps > target[1] * 1.3)
    vcopy = video_ok(v) and burn_index is None and not need_scale and not over_rate
    # multicanal : on fait nous-mêmes la réduction en stéréo (le mixage par défaut sature)
    acopy = bool(a and a["codec"] in BROWSER_AUDIO and (a.get("channels") or 2) <= 2)
    container = probe.get("container") or ""
    mp4 = "mp4" in container or "mov" in container
    first_audio = audios[0]["index"] if audios else None
    if vcopy and mp4 and (a is None or (acopy and a["index"] == first_audio)):
        mode = "direct"
    elif vcopy:
        mode = "copy"
    else:
        mode = "transcode"
    if target:
        height, kbps = min(src_h or target[0], target[0]), target[1]
    else:
        height = min(src_h or 1080, 1080)  # sans GPU, pas de 4K transcodé
        kbps = 10000 if height >= 1080 else 6000 if height >= 720 else 3000
    reasons = []
    if mode == "transcode":
        if v and v.get("codec") != "h264":
            reasons.append(f"vidéo {str(v.get('codec')).upper()}")
        elif v and not video_ok(v):
            reasons.append("H.264 10 bits")
        if burn_index is not None:
            reasons.append("sous-titres incrustés")
        if need_scale or over_rate:
            reasons.append(f"qualité {quality}")
    if mode != "direct" and a and not acopy:
        if (a.get("channels") or 2) > 2:
            reasons.append(f"audio {str(a['codec']).upper()} {a['channels']} canaux mixé en stéréo")
        else:
            reasons.append(f"audio {str(a['codec']).upper()} converti")
    return {"mode": mode, "video_index": (v or {}).get("index"), "audio_index": a["index"] if a else None,
            "acopy": acopy, "audio_channels": (a or {}).get("channels") or 2, "burn_index": burn_index, "height": height, "kbps": kbps,
            "src_height": src_h, "reasons": reasons}


def build_command(path, p, start, out_dir):
    cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "warning", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path]
    vi = p["video_index"]
    if p["mode"] == "copy":
        cmd += ["-map", f"0:{vi}", "-c:v", "copy"]
    else:
        scale = f"scale=-2:{p['height']}:flags=bicubic," if p["src_height"] > p["height"] else ""
        if p["burn_index"] is not None:
            cmd += ["-filter_complex", f"[0:{vi}][0:{p['burn_index']}]overlay=eof_action=pass[o];[o]{scale}format=yuv420p[v]",
                    "-map", "[v]"]
        else:
            cmd += ["-map", f"0:{vi}", "-vf", f"{scale}format=yuv420p"]
        k = p["kbps"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", f"{k}k", "-bufsize", f"{k * 2}k",
                "-profile:v", "high", "-level:v", "4.1", "-g", str(SEGMENT * 24), "-keyint_min", str(SEGMENT * 24),
                "-sc_threshold", "0", "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT})"]
    if p["audio_index"] is not None:
        cmd += ["-map", f"0:{p['audio_index']}"]
        if p["acopy"]:
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
            if p.get("audio_channels", 2) > 2:
                cmd += ["-af", DOWNMIX]
    else:
        cmd += ["-an"]
    cmd += ["-sn", "-dn", "-max_muxing_queue_size", "4096", "-f", "hls", "-hls_time", str(SEGMENT),
            "-hls_list_size", "0", "-hls_playlist_type", "event", "-hls_flags", "independent_segments+temp_file",
            "-start_number", "0", "-hls_segment_filename", os.path.join(out_dir, "s%05d.ts"),
            os.path.join(out_dir, "index.m3u8")]
    return cmd


class Session:
    def __init__(self, sid, path, p, start, out_dir):
        self.sid = sid
        self.path = path
        self.plan = p
        self.start = start
        self.dir = out_dir
        self.proc = None
        self.paused = False
        self.last_seg = 0
        self.last_access = time.time()
        self.log_file = None


class Transcoder:
    def __init__(self, work_dir, limits):
        self.work_dir = work_dir
        self.limits = limits  # callable -> (max_streams, max_transcodes)
        self.sessions = {}
        self.lock = threading.Lock()
        shutil.rmtree(work_dir, ignore_errors=True)
        os.makedirs(work_dir, exist_ok=True)
        self._stop = threading.Event()
        threading.Thread(target=self._throttle_loop, daemon=True).start()

    def counts(self):
        with self.lock:
            live = [s for s in self.sessions.values() if s.proc]
            return len(live), sum(1 for s in live if s.plan["mode"] == "transcode")

    def start(self, sid, path, p, offset_ms):
        if p["mode"] == "direct":
            return offset_ms
        max_streams, max_transcodes = self.limits()
        n, nt = self.counts()
        if n >= max_streams:
            raise Busy(f"Trop de lectures simultanées ({n}/{max_streams}).")
        if p["mode"] == "transcode" and nt >= max_transcodes:
            raise Busy(f"Trop de transcodages simultanés ({nt}/{max_transcodes}) : le processeur est déjà occupé.")
        seconds = offset_ms / 1000
        start = media.keyframe_before(path, seconds, 0) if p["mode"] == "copy" and seconds > 0 else seconds
        out_dir = os.path.join(self.work_dir, sid)
        os.makedirs(out_dir, exist_ok=True)
        s = Session(sid, path, p, start, out_dir)
        cmd = build_command(path, p, start, out_dir)
        log.debug("ffmpeg %s", " ".join(cmd))
        s.log_file = open(os.path.join(out_dir, "ffmpeg.log"), "wb")
        s.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=s.log_file,
                                  preexec_fn=lambda: os.nice(5))
        with self.lock:
            self.sessions[sid] = s
        return int(start * 1000)

    def get(self, sid):
        with self.lock:
            return self.sessions.get(sid)

    def _log_tail(self, s):
        try:
            with open(os.path.join(s.dir, "ffmpeg.log"), "rb") as f:
                lines = f.read().decode("utf-8", "replace").strip().splitlines()
            return lines[-1] if lines else "ffmpeg s'est arrêté"
        except OSError:
            return "ffmpeg s'est arrêté"

    def playlist(self, sid, timeout=90):
        s = self.get(sid)
        if not s:
            raise StreamError("Flux expiré")
        s.last_access = time.time()
        path = os.path.join(s.dir, "index.m3u8")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                if "#EXTINF" in text:
                    return text
            except OSError:
                pass
            if s.proc and s.proc.poll() is not None:
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                    if "#EXTINF" in text:
                        return text
                except OSError:
                    pass
                raise StreamError("Échec de la conversion : " + self._log_tail(s))
            time.sleep(0.15)
        raise StreamError("La conversion est trop lente à démarrer")

    def segment(self, sid, name, timeout=60):
        m = SEG_RE.match(name)
        s = self.get(sid)
        if not m or not s:
            raise StreamError("Segment introuvable")
        idx = int(m.group(1))
        s.last_access = time.time()
        s.last_seg = max(s.last_seg, idx) if idx >= s.last_seg - 5 else idx
        if s.paused and s.proc:
            self._resume(s)
        path = os.path.join(s.dir, name)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.isfile(path):
                return path
            if s.proc and s.proc.poll() is not None:
                break
            time.sleep(0.1)
        raise StreamError("Segment introuvable")

    def touch(self, sid):
        s = self.get(sid)
        if s:
            s.last_access = time.time()

    def _resume(self, s):
        try:
            os.kill(s.proc.pid, signal.SIGCONT)
        except OSError:
            pass
        s.paused = False

    def stop(self, sid):
        with self.lock:
            s = self.sessions.pop(sid, None)
        if not s:
            return
        if s.proc and s.proc.poll() is None:
            try:
                os.kill(s.proc.pid, signal.SIGCONT)
                s.proc.terminate()
                s.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    s.proc.kill()
                    s.proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if s.log_file:
            s.log_file.close()
        shutil.rmtree(s.dir, ignore_errors=True)

    def stop_all(self):
        self._stop.set()
        for sid in list(self.sessions):
            self.stop(sid)

    def idle(self, max_idle):
        now = time.time()
        with self.lock:
            return [sid for sid, s in self.sessions.items() if now - s.last_access > max_idle]

    def _throttle_loop(self):
        while not self._stop.wait(2):
            with self.lock:
                sessions = list(self.sessions.values())
            for s in sessions:
                if not s.proc or s.proc.poll() is not None:
                    continue
                try:
                    names = os.listdir(s.dir)
                except OSError:
                    continue
                idxs = sorted(int(m.group(1)) for m in map(SEG_RE.match, names) if m)
                if not idxs:
                    continue
                ahead = idxs[-1] - s.last_seg
                if not s.paused and ahead > AHEAD_PAUSE:
                    try:
                        os.kill(s.proc.pid, signal.SIGSTOP)
                        s.paused = True
                    except OSError:
                        pass
                elif s.paused and ahead < AHEAD_RESUME:
                    self._resume(s)
                for i in idxs:
                    if i >= s.last_seg - KEEP_BEHIND:
                        break
                    try:
                        os.remove(os.path.join(s.dir, f"s{i:05d}.ts"))
                    except OSError:
                        pass
