"""Appels à ffprobe / ffmpeg : analyse des fichiers, images, sous-titres."""
import json
import logging
import os
import shutil
import subprocess
import threading

log = logging.getLogger("seance.media")

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
IMAGE_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub", "pgssub"}
_image_slots = threading.BoundedSemaphore(3)  # limite les ffmpeg d'images simultanés
# Fichiers des bibliothèques : ffmpeg ne doit lire que des fichiers locaux. Un fichier piégé (une playlist HLS ou
# une liste « concat » déguisée en .mkv) pourrait sinon lui faire ouvrir des adresses réseau ou d'autres fichiers.
LOCAL_ONLY = ["-protocol_whitelist", "file,pipe"]
REFUSED_FORMATS = {"hls", "applehttp", "concat", "ffconcat", "tee"}


class MediaError(Exception):
    pass


_options = {}


def has_option(name):
    """ffmpeg connaît-il cette option ? (-readrate n'existe que depuis ffmpeg 5)"""
    if name not in _options:
        try:
            out = subprocess.run([FFMPEG, "-hide_banner", "-h", "full"], capture_output=True, timeout=20).stdout
            _options[name] = f"-{name} ".encode() in out
        except (OSError, subprocess.SubprocessError):
            _options[name] = False
    return _options[name]


def available():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run(cmd, timeout=120, input_bytes=None):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, input=input_bytes)
    except subprocess.TimeoutExpired:
        raise MediaError("délai dépassé")
    except FileNotFoundError:
        raise MediaError("ffmpeg est introuvable (apt install ffmpeg)")
    if p.returncode != 0:
        raise MediaError((p.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["échec"])
    return p.stdout


def _lang(tags):
    from .names import LANG_WORDS
    code = (tags.get("language") or "").lower()
    if code in ("und", "unk", "mis", "zxx"):
        return ""
    return LANG_WORDS.get(code, code)


FONT_EXT = (".ttf", ".otf", ".ttc", ".otc", ".woff", ".woff2")
FONT_MIME = ("font/", "application/x-truetype-font", "application/x-font", "application/vnd.ms-opentype",
             "application/font-sfnt", "application/x-opentype")


def _fps(rate):
    try:
        num, den = (rate or "0/1").split("/")
        return round(int(num) / int(den), 3) if int(den) else 0
    except (ValueError, ZeroDivisionError):
        return 0


def attachment_info(s, tags=None):
    """Polices jointes au MKV (utilisées par les sous-titres ASS des animes)."""
    tags = tags if tags is not None else {k.lower(): v for k, v in (s.get("tags") or {}).items()}
    name = (tags.get("filename") or "").strip()
    mime = (tags.get("mimetype") or "").lower()
    ext = os.path.splitext(name)[1].lower()
    if ext in FONT_EXT or mime.startswith(FONT_MIME) or s.get("codec_name") in ("ttf", "otf"):
        return {"index": s["index"], "filename": name[:200], "ext": ext if ext in FONT_EXT else ".ttf"}
    return None


def attachments(path):
    out = run([FFPROBE, "-v", "error", *LOCAL_ONLY, "-print_format", "json", "-show_streams", "-select_streams", "t", path],
              timeout=60)
    return [a for a in (attachment_info(s) for s in json.loads(out or b"{}").get("streams", [])) if a]


def probe(path):
    out = run([FFPROBE, "-v", "error", *LOCAL_ONLY, "-print_format", "json", "-show_format", "-show_streams",
               "-show_chapters", path], timeout=90)
    data = json.loads(out or b"{}")
    fmt = data.get("format", {})
    if set((fmt.get("format_name") or "").split(",")) & REFUSED_FORMATS:
        raise MediaError("liste de lecture déguisée en vidéo : fichier ignoré")
    info = {
        "container": fmt.get("format_name", ""),
        "duration": float(fmt.get("duration") or 0),
        "bitrate": int(fmt.get("bit_rate") or 0),
        "video": None, "audio": [], "subs": [], "external": [], "chapters": [], "attachments": [],
    }
    for c in data.get("chapters", []):
        try:
            start, end = float(c.get("start_time") or 0), float(c.get("end_time") or 0)
        except (TypeError, ValueError):
            continue
        title = ((c.get("tags") or {}).get("title") or "").strip()
        if end > start:
            info["chapters"].append({"start": round(start, 3), "end": round(end, 3), "title": title[:120]})
    for s in data.get("streams", []):
        kind = s.get("codec_type")
        tags = {k.lower(): v for k, v in (s.get("tags") or {}).items()}
        disp = s.get("disposition") or {}
        if kind == "video" and info["video"] is None and not disp.get("attached_pic"):
            info["video"] = {
                "index": s["index"], "codec": s.get("codec_name"), "profile": s.get("profile") or "",
                "pix_fmt": s.get("pix_fmt") or "", "width": s.get("width") or 0, "height": s.get("height") or 0,
                "bitrate": int(s.get("bit_rate") or 0), "fps": _fps(s.get("avg_frame_rate") or s.get("r_frame_rate")),
                # pour les clients Jellyfin (profil de l'appareil : niveau, HDR, images de référence)
                "level": s.get("level") if isinstance(s.get("level"), int) and s.get("level") > 0 else None,
                "transfer": s.get("color_transfer") or "", "refs": s.get("refs") or None,
                "interlaced": (s.get("field_order") or "progressive") not in ("progressive", "unknown"),
            }
        elif kind == "audio":
            info["audio"].append({
                "index": s["index"], "codec": s.get("codec_name"), "channels": s.get("channels") or 2,
                "layout": s.get("channel_layout") or "", "rate": int(s.get("sample_rate") or 0),
                "lang": _lang(tags), "title": tags.get("title") or "", "default": bool(disp.get("default")),
            })
        elif kind == "attachment":
            att = attachment_info(s, tags)
            if att:
                info["attachments"].append(att)
        elif kind == "subtitle":
            info["subs"].append({
                "index": s["index"], "codec": s.get("codec_name") or "", "lang": _lang(tags),
                "title": tags.get("title") or "", "forced": bool(disp.get("forced")),
                "default": bool(disp.get("default")),
            })
    if not info["duration"] and info["video"] is None and not info["audio"]:
        raise MediaError("fichier illisible")
    return info


def keyframe_before(path, seconds, video_index=0):
    """Instant de la dernière image clé <= seconds (pour couper proprement en copie)."""
    if seconds <= 0:
        return 0.0
    start = max(0.0, seconds - 30)
    try:
        out = run([FFPROBE, "-v", "error", *LOCAL_ONLY, "-select_streams", f"v:{video_index}", "-skip_frame", "nokey",
                   "-show_entries", "frame=pts_time,best_effort_timestamp_time", "-of", "csv=p=0",
                   "-read_intervals", f"{start:.3f}%{seconds + 0.5:.3f}", path], timeout=30)
    except MediaError:
        return seconds
    best = None
    for line in out.decode().splitlines():
        for v in line.split(","):
            try:
                t = float(v)
            except ValueError:
                continue
            if t <= seconds + 0.05 and (best is None or t > best):
                best = t
            break
    return best if best is not None else seconds


def resize_image(src, dest, w, h, crop=True):
    """Redimensionne (et recadre) une image avec ffmpeg -> JPEG."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}" if crop
          else f"scale={w}:{h}:force_original_aspect_ratio=decrease")
    tmp = f"{dest}.{threading.get_ident()}.tmp.jpg"
    with _image_slots:
        run([FFMPEG, "-y", "-v", "error", "-i", src, "-vf", vf, "-frames:v", "1", "-q:v", "4", tmp], timeout=60)
    _keep_image(tmp, dest)


def grab_frame(video, dest, at_seconds, w, h):
    tmp = f"{dest}.{threading.get_ident()}.tmp.jpg"
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    with _image_slots:
        run([FFMPEG, "-y", "-v", "error", *LOCAL_ONLY, "-ss", f"{at_seconds:.2f}", "-i", video, "-vf", vf,
             "-frames:v", "1", "-q:v", "4", tmp], timeout=90)
    _keep_image(tmp, dest)


def _keep_image(tmp, dest):
    """ffmpeg peut réussir sans rien écrire (instant après la dernière image, piste vidéo vide)."""
    if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise MediaError("aucune image à cet instant de la vidéo")
    os.replace(tmp, dest)


def extract_subtitle(source, stream_index=None):
    """Convertit une piste (interne) ou un fichier (externe) de sous-titres texte en SRT."""
    cmd = [FFMPEG, "-v", "error", *LOCAL_ONLY, "-i", source]
    if stream_index is not None:
        cmd += ["-map", f"0:{stream_index}"]
    cmd += ["-c:s", "srt", "-f", "srt", "-"]
    return run(cmd, timeout=600)
