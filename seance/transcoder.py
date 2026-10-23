"""Lecture : décision direct / remux / transcodage et flux HLS « dynamiques », sur le modèle de Jellyfin.

Choix repris de Jellyfin (DynamicHlsController / EncodingHelper) :
- une playlist VOD complète est annoncée d'emblée (segments de 6 s ; en remux, calés sur les images clés
  lues dans l'index du MKV) : le lecteur connaît toute la chronologie et avance/recule sans relancer le flux ;
- les segments sont fabriqués à la demande en fMP4 (pas de conversion TS -> MP4 dans le navigateur) avec les
  horodatages d'origine (-copyts) ; si le lecteur demande un segment loin de ce que ffmpeg prépare,
  ffmpeg est relancé à cet endroit (-ss + -start_number) ;
- en remux, on vise 0,25 s après l'image clé (sinon ffmpeg retombe sur la précédente) ;
- options d'entrée tolérantes (analyzeduration / probesize élevés, +genpts), mixages stéréo de Jellyfin.

Sans GPU, tout l'enjeu reste de ne transcoder la vidéo que lorsque c'est indispensable :
- direct    : le navigateur lit le fichier tel quel                                  -> 0 % CPU
- copy      : vidéo recopiée (H.264, et HEVC/AV1 si le navigateur sait les lire)     -> très peu de CPU
- transcode : codec illisible par le navigateur, sous-titres image incrustés, qualité réduite -> libx264
"""
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time

from . import media
from .keyframes import equal_segments, segments_from_keyframes

log = logging.getLogger("seance.transcode")

QUALITY = {"original": None, "1080p": (1080, 8000), "720p": (720, 4000), "480p": (480, 1500)}
SEGMENT = 6            # secondes (valeur par défaut de Jellyfin)
COPY_SEEK_PAD = 0.25   # remux : viser juste après l'image clé
SEG_RE = re.compile(r"^(\d{1,6})\.m4s$")
INIT = "init.mp4"
AHEAD_PAUSE = 30       # segments d'avance (~3 min) avant de mettre ffmpeg en pause
AHEAD_RESUME = 15
KEEP_BEHIND = 20       # segments gardés derrière la position (~2 min)
RESTART_GAP = 4        # segments (24 s) : au-delà, on relance ffmpeg là où le lecteur demande
INPUT_OPTS = ["-analyzeduration", "200M", "-probesize", "1G"]

# Navigateur sans profil déclaré : ce que tous savent lire
DEFAULT_CAPS = {"video": {"h264"}, "audio": {"aac", "mp3"}, "containers": {"mp4", "mov"}}

# Réductions en stéréo de Jellyfin (DownMixAlgorithmsHelper) : (algorithme, disposition) -> filtre
DOWNMIX_FILTERS = {
    ("dave750", "5.1"): "pan=stereo|c0=0.5*c2+0.707*c0+0.707*c4+0.5*c3|c1=0.5*c2+0.707*c1+0.707*c5+0.5*c3",
    ("dave750", "7.1"): "pan=5.1(side)|c0=c0|c1=c1|c2=c2|c3=c3|c4=0.707*c4+0.707*c6|c5=0.707*c5+0.707*c7,"
                        "pan=stereo|c0=0.5*c2+0.707*c0+0.707*c4+0.5*c3|c1=0.5*c2+0.707*c1+0.707*c5+0.5*c3",
    ("nightmode", "5.1"): "pan=stereo|c0=c2+0.30*c0+0.30*c4|c1=c2+0.30*c1+0.30*c5",
    ("nightmode", "7.1"): "pan=5.1(side)|c0=c0|c1=c1|c2=c2|c3=c3|c4=0.707*c4+0.707*c6|c5=0.707*c5+0.707*c7,"
                          "pan=stereo|c0=c2+0.30*c0+0.30*c4|c1=c2+0.30*c1+0.30*c5",
    ("rfc7845", "3.0"): "pan=stereo|c0=0.414214*c2+0.585786*c0|c1=0.414214*c2+0.585786*c1",
    ("rfc7845", "quad"): "pan=stereo|c0=0.422650*c0+0.366025*c2+0.211325*c3|c1=0.422650*c1+0.366025*c3+0.211325*c2",
    ("rfc7845", "5.0"): "pan=stereo|c0=0.460186*c2+0.650802*c0+0.563611*c3+0.325401*c4|"
                        "c1=0.460186*c2+0.650802*c1+0.563611*c4+0.325401*c3",
    ("rfc7845", "5.1"): "pan=stereo|c0=0.374107*c2+0.529067*c0+0.458186*c4+0.264534*c5+0.374107*c3|"
                        "c1=0.374107*c2+0.529067*c1+0.458186*c5+0.264534*c4+0.374107*c3",
    ("rfc7845", "6.1"): "pan=stereo|c0=0.321953*c2+0.455310*c0+0.394310*c5+0.227655*c6+0.278819*c4+0.321953*c3|"
                        "c1=0.321953*c2+0.455310*c1+0.394310*c6+0.227655*c5+0.278819*c4+0.321953*c3",
    ("rfc7845", "7.1"): "pan=stereo|c0=0.274804*c2+0.388631*c0+0.336565*c6+0.194316*c7+0.336565*c4+0.194316*c5"
                        "+0.274804*c3|c1=0.274804*c2+0.388631*c1+0.336565*c7+0.194316*c6+0.336565*c5+0.194316*c4"
                        "+0.274804*c3",
    ("ac4", "3.0"): "pan=stereo|c0=c0+0.707*c2|c1=c1+0.707*c2",
    ("ac4", "5.0"): "pan=stereo|c0=c0+0.707*c2+0.707*c3|c1=c1+0.707*c2+0.707*c4",
    ("ac4", "5.1"): "pan=stereo|c0=c0+0.707*c2+0.707*c4|c1=c1+0.707*c2+0.707*c5",
    ("ac4", "7.0"): "pan=5.0(side)|c0=c0|c1=c1|c2=c2|c3=0.707*c3+0.707*c5|c4=0.707*c4+0.707*c6,"
                    "pan=stereo|c0=c0+0.707*c2+0.707*c3|c1=c1+0.707*c2+0.707*c4",
    ("ac4", "7.1"): "pan=5.1(side)|c0=c0|c1=c1|c2=c2|c3=c3|c4=0.707*c4+0.707*c6|c5=0.707*c5+0.707*c7,"
                    "pan=stereo|c0=c0+0.707*c2+0.707*c4|c1=c1+0.707*c2+0.707*c5",
}
DOWNMIX_ALGOS = ("none", "dave750", "nightmode", "rfc7845", "ac4")
# notre touche : un limiteur APRÈS le mixage et le gain, pour qu'aucun réglage ne puisse saturer
LIMITER = "alimiter=limit=0.8:attack=5:release=80:level=disabled"
# compat : ancien nom utilisé par les tests
DOWNMIX = "aformat=channel_layouts=stereo,volume=2," + LIMITER


class Busy(Exception):
    pass


class StreamError(Exception):
    pass


def infer_layout(a):
    lay = (a.get("layout") or "").split("(")[0]
    if lay:
        return lay
    return {1: "mono", 2: "stereo", 3: "2.1", 4: "4.0", 5: "5.0", 6: "5.1", 7: "6.1", 8: "7.1"}.get(a.get("channels") or 2, "")


def audio_filter(a, algo="none", boost=2.0):
    """Chaîne -af pour réduire une piste multicanal en stéréo (vide si déjà stéréo)."""
    if not a or (a.get("channels") or 2) <= 2:
        return ""
    parts = []
    pan = DOWNMIX_FILTERS.get((algo, infer_layout(a)))
    parts.append(pan or "aformat=channel_layouts=stereo")
    if abs(boost - 1.0) > 0.01:
        parts.append(f"volume={boost:g}")
    parts.append(LIMITER)
    return ",".join(parts)


def _bit_depth(v):
    prof = (v.get("profile") or "").lower()
    fmt = (v.get("pix_fmt") or "")
    return 10 if ("10" in prof or "10le" in fmt or "10be" in fmt or "12" in fmt) else 8


def video_ok(v, caps=None):
    """La piste vidéo peut-elle être recopiée telle quelle pour ce navigateur ?"""
    caps = caps or DEFAULT_CAPS
    if not v:
        return False
    codec, depth = v.get("codec"), _bit_depth(v)
    fmt = v.get("pix_fmt") or "yuv420p"
    if "422" in fmt or "444" in fmt or "4:2:2" in (v.get("profile") or "") or "4:4:4" in (v.get("profile") or ""):
        return False
    if codec == "h264":
        return depth == 8 and "h264" in caps["video"]
    if codec == "hevc":
        return ("hevc10" if depth == 10 else "hevc") in caps["video"] and depth <= 10
    if codec == "av1":
        return ("av1_10" if depth == 10 else "av1") in caps["video"]
    if codec == "vp9":
        return "vp9" in caps["video"] and depth == 8
    return False


def plan(probe, audio_index=None, burn_index=None, quality="original", caps=None, algo="none", boost=2.0):
    caps = caps or DEFAULT_CAPS
    v = probe.get("video")
    audios = probe.get("audio") or []
    a = next((x for x in audios if x["index"] == audio_index), audios[0] if audios else None)
    target = QUALITY.get(quality)
    src_h = (v or {}).get("height") or 0
    rate_kbps = int((probe.get("bitrate") or 0) / 1000)
    need_scale = bool(target and src_h > target[0])
    over_rate = bool(target and rate_kbps and rate_kbps > target[1] * 1.3)
    vcopy = video_ok(v, caps) and burn_index is None and not need_scale and not over_rate
    # multicanal : on fait nous-mêmes la réduction en stéréo (le mixage par défaut du navigateur sature)
    # (un appareil Jellyfin peut déclarer lire le multicanal : max_channels)
    acopy = bool(a and a["codec"] in caps["audio"] and (a.get("channels") or 2) <= caps.get("max_channels", 2))
    container = (probe.get("container") or "").split(",")
    # MKV : jamais en direct (les navigateurs ne le lisent pas), c'est le remux qui s'en charge
    direct_container = bool(set(container) & set(caps.get("containers") or ()) & {"mp4", "mov"})
    first_audio = audios[0]["index"] if audios else None
    if vcopy and direct_container and (a is None or (acopy and a["index"] == first_audio)):
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
        if v and not video_ok(v, caps):
            depth = _bit_depth(v)
            reasons.append(f"vidéo {str(v.get('codec')).upper()}{' 10 bits' if depth == 10 else ''} "
                           "illisible par ce navigateur")
        if burn_index is not None:
            reasons.append("sous-titres incrustés")
        if need_scale or over_rate:
            reasons.append(f"qualité {quality}")
    if mode != "direct" and a and not acopy:
        if (a.get("channels") or 2) > 2:
            reasons.append(f"audio {str(a['codec']).upper()} {a['channels']} canaux mixé en stéréo")
        else:
            reasons.append(f"audio {str(a['codec']).upper()} converti")
    fps = (v or {}).get("fps") or 0
    seglen = float(SEGMENT)
    if mode == "transcode" and fps and abs(fps - math.floor(fps + 0.001)) > 0.001:
        # 23,976 i/s : le GOP forcé dure 6,006 s, la playlist doit l'annoncer (comme Jellyfin)
        seglen = math.ceil(SEGMENT * 1000 * math.ceil(fps) / fps) / 1000
    return {"mode": mode, "video_index": (v or {}).get("index"), "video_codec": (v or {}).get("codec"),
            "audio_index": a["index"] if a else None, "acopy": acopy, "audio_codec": (a or {}).get("codec"),
            "audio_channels": (a or {}).get("channels") or 2, "audio_filter": audio_filter(a, algo, boost) if a else "",
            "burn_index": burn_index, "height": height, "kbps": kbps, "src_height": src_h,
            "src_width": (v or {}).get("width") or 0, "fps": fps, "seglen": seglen, "reasons": reasons}


def build_command(path, p, start, out_dir, start_number=0):
    """Commande ffmpeg d'un flux HLS fMP4 démarrant à `start` (secondes) au segment `start_number`."""
    cmd = [media.FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "warning", "-y", *INPUT_OPTS]
    if p["mode"] == "copy":
        cmd += ["-fflags", "+genpts"]
    if p["burn_index"] is not None and p.get("src_width") and p.get("src_height"):
        # sous-titres image (PGS…) : taille du canevas connue d'avance (« unspecified size » sinon)
        cmd += ["-canvas_size", f"{p['src_width']}x{p['src_height']}"]
    if start > 0:
        # En copie, ffmpeg recule de ~0,13 s avant de chercher et retombait sur l'image clé PRÉCÉDENTE
        # (Jellyfin ajoute 0,5 s pour la même raison) : on vise juste après l'image clé voulue.
        ss = start + COPY_SEEK_PAD if p["mode"] == "copy" else start
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += [*media.LOCAL_ONLY, "-i", path, "-map_metadata", "-1", "-map_chapters", "-1", "-threads", "0"]
    vi = p["video_index"]
    seglen = p.get("seglen") or SEGMENT
    if p["mode"] == "copy":
        cmd += ["-map", f"0:{vi}", "-codec:v:0", "copy", "-start_at_zero"]
        if p.get("video_codec") == "hevc":
            cmd += ["-tag:v:0", "hvc1"]
    else:
        scale = f"scale=-2:{p['height']}:flags=bicubic," if p["src_height"] > p["height"] else ""
        if p["burn_index"] is not None:
            cmd += ["-filter_complex",
                    f"[0:{p['burn_index']}]format=yuva420p[s];"
                    f"[0:{vi}][s]overlay=eof_action=pass:repeatlast=0[o];[o]{scale}format=yuv420p[v]",
                    "-map", "[v]"]
        else:
            cmd += ["-map", f"0:{vi}", "-vf", f"{scale}format=yuv420p"]
        k = p["kbps"]
        gop = math.ceil(seglen * (p.get("fps") or 24))
        cmd += ["-codec:v:0", "libx264", "-preset", "veryfast", "-crf", "23", "-maxrate", f"{k}k",
                "-bufsize", f"{k * 2}k", "-profile:v", "high", "-level:v", "4.1",
                "-force_key_frames:0", f"expr:gte(t,n_forced*{seglen:g})", "-sc_threshold:v:0", "0",
                "-g:v:0", str(gop), "-keyint_min:v:0", str(gop), "-start_at_zero"]
    if p["audio_index"] is not None:
        cmd += ["-map", f"0:{p['audio_index']}"]
        if p["acopy"]:
            cmd += ["-codec:a:0", "copy"]
            if p.get("audio_codec") in ("flac", "opus"):
                cmd += ["-strict", "-2"]
        else:
            cmd += ["-codec:a:0", "aac", "-ac", "2", "-ab", "192000", "-ar", "48000"]
            if p.get("audio_filter"):
                cmd += ["-af", p["audio_filter"]]
    else:
        cmd += ["-an"]
    seg_opt = "-hls_segment_options" if media.has_option("hls_segment_options") else "-hls_ts_options"
    cmd += ["-sn", "-dn", "-copyts", "-avoid_negative_ts", "disabled", "-max_muxing_queue_size", "2048",
            "-f", "hls", "-max_delay", "5000000", "-hls_time", f"{seglen:g}", "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", INIT, "-start_number", str(start_number),
            "-hls_segment_filename", os.path.join(out_dir, "%d.m4s"), "-hls_playlist_type", "vod",
            "-hls_list_size", "0", seg_opt, "movflags=+frag_discont+skip_sidx", "-hls_flags", "temp_file",
            os.path.join(out_dir, "ffmpeg.m3u8")]
    return cmd


class Session:
    def __init__(self, sid, path, p, segments, out_dir):
        self.sid = sid
        self.path = path
        self.plan = p
        self.segments = segments    # [(début, durée)] en secondes
        self.dir = out_dir
        self.proc = None
        self.run_start = 0          # premier segment de l'ffmpeg en cours
        self.paused = False
        self.active = 0.0           # secondes de travail réel de ffmpeg (hors pauses)
        self.speed = None           # vitesse de production (x temps réel)
        self.last_req = 0
        self.last_access = time.time()
        self.log_file = None
        self.start_index = 0
        self.lock = threading.Lock()

    def produced(self):
        try:
            names = os.listdir(self.dir)
        except OSError:
            return []
        return sorted(int(m.group(1)) for m in map(SEG_RE.match, names) if m)


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

    def start(self, sid, path, p, offset_ms, duration_ms=0, keyframes=None, lazy=False):
        """Prépare un flux et lance ffmpeg au segment de `offset_ms`. -> position de départ (ms).

        lazy : ffmpeg ne démarre qu'à la première demande du lecteur (clients Jellyfin, qui peuvent préparer
        l'élément suivant d'une file d'attente sans le lire tout de suite)."""
        if p["mode"] == "direct":
            return offset_ms
        max_streams, max_transcodes = self.limits()
        n, nt = self.counts()
        if n >= max_streams:
            raise Busy(f"Trop de lectures simultanées ({n}/{max_streams}).")
        if p["mode"] == "transcode" and nt >= max_transcodes:
            raise Busy(f"Trop de transcodages simultanés ({nt}/{max_transcodes}) : le processeur est déjà occupé.")
        duration = (duration_ms or 0) / 1000
        if p["mode"] == "copy" and keyframes:
            kf_dur, kf = keyframes
            segments = segments_from_keyframes(kf, duration or kf_dur, p.get("seglen") or SEGMENT)
        else:
            segments = equal_segments(duration or 3600 * 4, p.get("seglen") or SEGMENT)
        out_dir = os.path.join(self.work_dir, sid)
        os.makedirs(out_dir, exist_ok=True)
        s = Session(sid, path, p, segments, out_dir)
        with self.lock:
            self.sessions[sid] = s
        first = self.index_at(s, offset_ms / 1000)
        s.start_index = first
        if not lazy:
            with s.lock:
                self._spawn(s, first)
        return offset_ms

    @staticmethod
    def index_at(s, seconds):
        lo, hi, found = 0, len(s.segments) - 1, 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if s.segments[mid][0] <= seconds + 0.001:
                found = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return found

    def _kill(self, s):
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
        s.paused = False

    def _spawn(self, s, index):
        """(Re)lance ffmpeg pour produire à partir du segment `index` (verrou de session tenu)."""
        self._kill(s)
        for i in s.produced():
            if i >= index:  # segments d'une autre passe : on ne mélange pas deux découpages
                try:
                    os.remove(os.path.join(s.dir, f"{i}.m4s"))
                except OSError:
                    pass
        start = s.segments[index][0] if s.segments else 0
        cmd = build_command(s.path, s.plan, start, s.dir, index)
        log.debug("ffmpeg %s", " ".join(cmd))
        if s.log_file:
            s.log_file.close()
        s.log_file = open(os.path.join(s.dir, "ffmpeg.log"), "ab")
        s.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=s.log_file,
                                  preexec_fn=lambda: os.nice(5))
        s.run_start = index
        s.last_req = index
        s.active = 0.0
        s.speed = None

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

    def playlist(self, sid):
        """Playlist VOD complète, comme celle de Jellyfin."""
        s = self.get(sid)
        if not s:
            raise StreamError("Flux expiré")
        s.last_access = time.time()
        target = math.ceil(max((d for _, d in s.segments), default=SEGMENT))
        lines = ["#EXTM3U", "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{target}",
                 "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-INDEPENDENT-SEGMENTS", f'#EXT-X-MAP:URI="{INIT}"']
        for n, (_, d) in enumerate(s.segments):
            lines += [f"#EXTINF:{d:.6f},", f"{n}.m4s"]
        lines.append("#EXT-X-ENDLIST")
        return "\n".join(lines) + "\n"

    def _wait_file(self, s, path, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.isfile(path):
                return path
            proc = s.proc
            if proc and proc.poll() is not None and proc is s.proc:
                time.sleep(0.2)  # dernier renommage du fichier temporaire
                if os.path.isfile(path):
                    return path
                if proc is s.proc:
                    raise StreamError("Échec de la conversion : " + self._log_tail(s))
            if s.sid not in self.sessions:
                raise StreamError("Flux arrêté")
            time.sleep(0.1)
        raise StreamError("La conversion est trop lente")

    def init(self, sid, timeout=90):
        s = self.get(sid)
        if not s:
            raise StreamError("Flux expiré")
        s.last_access = time.time()
        with s.lock:
            if not s.proc:
                self._spawn(s, s.start_index)
            first = s.run_start
        # l'en-tête est écrit d'un bloc mais sans fichier temporaire : on attend le premier segment
        # de la passe (l'en-tête est alors forcément complet)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if any(i >= first for i in s.produced()) or (s.proc and s.proc.poll() is not None):
                break
            time.sleep(0.1)
        return self._wait_file(s, os.path.join(s.dir, INIT), max(1, deadline - time.time()))

    def segment(self, sid, name, timeout=90):
        m = SEG_RE.match(name)
        s = self.get(sid)
        if not m or not s:
            raise StreamError("Segment introuvable")
        idx = int(m.group(1))
        if idx >= len(s.segments):
            raise StreamError("Segment introuvable")
        s.last_access = time.time()
        path = os.path.join(s.dir, name)
        with s.lock:
            s.last_req = idx
            if not os.path.isfile(path):
                produced = [i for i in s.produced() if i >= s.run_start]
                current = produced[-1] if produced else s.run_start - 1
                running = s.proc and s.proc.poll() is None
                if not running or idx < s.run_start or idx > current + RESTART_GAP:
                    # le lecteur a sauté : on relance ffmpeg là où il demande (comme Jellyfin)
                    self._spawn(s, idx)
            if s.paused:
                self._resume(s)
        return self._wait_file(s, path, timeout)

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
        with s.lock:
            self._kill(s)
        if s.log_file:
            s.log_file.close()
        shutil.rmtree(s.dir, ignore_errors=True)

    def stop_all(self):
        self._stop.set()
        for sid in list(self.sessions):
            self.stop(sid)

    def stats(self, sid):
        s = self.get(sid)
        if not s:
            return {}
        try:
            with open(os.path.join(s.dir, "ffmpeg.log"), "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 1500))
                warnings = f.read().decode("utf-8", "replace").strip().splitlines()[-4:]
        except OSError:
            warnings = []
        return {"speed": s.speed, "paused": s.paused, "warnings": warnings}

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
                produced = s.produced()
                mine = [i for i in produced if i >= s.run_start]
                if not s.paused:
                    s.active += 2
                if mine and s.active >= 10 and not s.paused:
                    done = sum(d for _, d in s.segments[s.run_start:mine[-1] + 1])
                    s.speed = round(done / s.active, 2)
                ahead = (mine[-1] if mine else s.run_start) - s.last_req
                if not s.paused and ahead > AHEAD_PAUSE:
                    try:
                        os.kill(s.proc.pid, signal.SIGSTOP)
                        s.paused = True
                    except OSError:
                        pass
                elif s.paused and ahead < AHEAD_RESUME:
                    self._resume(s)
                for i in produced:
                    if i >= s.last_req - KEEP_BEHIND:
                        break
                    try:
                        os.remove(os.path.join(s.dir, f"{i}.m4s"))
                    except OSError:
                        pass
