"""Profil d'appareil des clients Jellyfin : que peut lire l'appareil tel quel, que faut-il convertir ?

Reprend la logique du StreamBuilder de Jellyfin, réduite à ce que Séance sait produire :
- lecture directe : le fichier est envoyé tel quel (conteneur, codecs et conditions du profil respectés) ;
- remux (« direct stream ») : la vidéo est recopiée dans un flux HLS fMP4, l'audio recopié ou converti ;
- transcodage : la vidéo est réencodée en H.264 (seul codec que Séance produit).
"""

# noms des codecs côté Jellyfin (ffprobe -> Jellyfin)
SUB_FORMATS = {"subrip": "srt", "webvtt": "vtt", "hdmv_pgs_subtitle": "pgssub", "dvd_subtitle": "dvdsub",
               "dvb_subtitle": "dvbsub", "mov_text": "mov_text", "ass": "ass", "ssa": "ssa", "srt": "srt",
               "text": "srt", "microdvd": "sub"}
TEXT_FORMATS = {"srt", "vtt", "ass", "ssa", "mov_text", "sub"}
IMAGE_FORMATS = {"pgssub", "dvdsub", "dvbsub"}


def split(value):
    if isinstance(value, (list, tuple)):
        value = ",".join(str(v) for v in value)
    return [v.strip().lower() for v in str(value or "").split(",") if v.strip()]


def contains(listing, value):
    """Liste de conteneurs / codecs d'un profil Jellyfin : vide = tout, « -a,b » = tout sauf a et b."""
    items = split(listing)
    if not items:
        return True
    value = (value or "").lower()
    if items[0].startswith("-"):
        excluded = {items[0][1:], *items[1:]}
        return value not in excluded
    return value in items


def container_name(probe, path=""):
    """Conteneur au sens de Jellyfin (mkv, mp4, webm, avi, ts…)."""
    names = split(probe.get("container"))
    ext = (path or "").rsplit(".", 1)[-1].lower() if "." in (path or "") else ""
    if "matroska" in names or "webm" in names:
        return "webm" if ext == "webm" else "mkv"
    if "mov" in names or "mp4" in names:
        return {"m4v": "m4v", "mov": "mov"}.get(ext, "mp4")
    if "mpegts" in names:
        return "ts"
    if "avi" in names:
        return "avi"
    if "asf" in names:
        return "wmv" if ext == "wmv" else "asf"
    return ext or (names[0] if names else "")


def bit_depth(v):
    prof = (v.get("profile") or "").lower()
    fmt = v.get("pix_fmt") or ""
    if "12" in fmt:
        return 12
    return 10 if ("10" in prof or "10le" in fmt or "10be" in fmt) else 8


def range_type(v):
    t = (v.get("transfer") or "").lower()
    if t == "smpte2084":
        return "HDR10"
    if t == "arib-std-b67":
        return "HLG"
    return "SDR"


def video_values(v, probe):
    return {
        "videoprofile": (v.get("profile") or "").lower() or None,
        "videolevel": v.get("level"),
        "videobitdepth": bit_depth(v),
        "width": v.get("width") or None,
        "height": v.get("height") or None,
        "refframes": v.get("refs"),
        "videorangetype": range_type(v),
        "videobitrate": v.get("bitrate") or probe.get("bitrate") or None,
        "videoframerate": v.get("fps") or None,
        "isinterlaced": bool(v.get("interlaced")),
        "isanamorphic": False,
        "isavc": (v.get("codec") == "h264"),
        "numvideostreams": 1,
        "videocodectag": None,
        "videotimestamp": None,
    }


def audio_values(a, secondary=False):
    return {
        "audiochannels": a.get("channels") or 2,
        "audiosamplerate": a.get("rate") or None,
        "audioprofile": None,
        "audiobitrate": None,
        "audiobitdepth": None,
        "issecondaryaudio": secondary,
        "numaudiostreams": 1,
    }


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def condition_ok(cond, values):
    prop = str(cond.get("Property") or "").lower()
    kind = str(cond.get("Condition") or "")
    expected = cond.get("Value")
    if prop not in values:
        return True  # propriété inconnue de Séance : on ne bloque pas
    actual = values[prop]
    if actual is None or actual == "":
        return not cond.get("IsRequired")
    if isinstance(actual, bool):
        exp = str(expected).strip().lower() == "true"
        if kind == "Equals":
            return actual == exp
        if kind == "NotEquals":
            return actual != exp
        return True
    if isinstance(actual, (int, float)):
        if kind == "EqualsAny":
            return any(_num(x) == actual for x in str(expected or "").split("|"))
        e = _num(expected)
        if e is None:
            return True
        return {"Equals": actual == e, "NotEquals": actual != e, "LessThanEqual": actual <= e,
                "GreaterThanEqual": actual >= e}.get(kind, True)
    actual = str(actual).lower()
    if kind == "EqualsAny":
        return actual in [x.strip().lower() for x in str(expected or "").split("|")]
    if kind == "Equals":
        return actual == str(expected or "").lower()
    if kind == "NotEquals":
        return actual != str(expected or "").lower()
    return True


def conditions_ok(conds, values):
    return all(condition_ok(c, values) for c in conds or [] if isinstance(c, dict))


def codec_profiles_ok(profile, kind, codec, container, values):
    """Conditions des CodecProfiles (Type Video / VideoAudio) applicables à ce codec."""
    for cp in profile.get("CodecProfiles") or []:
        if not isinstance(cp, dict) or cp.get("Type") != kind:
            continue
        if not contains(cp.get("Codec"), codec) or not contains(cp.get("Container"), container):
            continue
        if not conditions_ok(cp.get("ApplyConditions"), values):
            continue
        if not conditions_ok(cp.get("Conditions"), values):
            return False
    return True


def first_audio(probe):
    audios = probe.get("audio") or []
    return audios[0]["index"] if audios else None


def can_direct_play(profile, probe, path, audio, max_bitrate):
    """La lecture directe du fichier est-elle possible ? -> (oui/non, raison)."""
    container = container_name(probe, path)
    v = probe.get("video")
    if not v:
        return False, "pas de vidéo"
    vcodec = v.get("codec") or ""
    acodec = (audio or {}).get("codec") or ""
    bitrate = probe.get("bitrate") or 0
    if max_bitrate and bitrate and bitrate > max_bitrate:
        return False, "débit trop élevé"
    match = None
    for dp in profile.get("DirectPlayProfiles") or []:
        if not isinstance(dp, dict) or dp.get("Type", "Video") != "Video":
            continue
        if contains(dp.get("Container"), container) and contains(dp.get("VideoCodec"), vcodec) and \
                (not audio or contains(dp.get("AudioCodec"), acodec)):
            match = dp
            break
    if not match:
        return False, f"conteneur {container} / {vcodec} / {acodec or '-'} non pris en charge"
    if not codec_profiles_ok(profile, "Video", vcodec, container, video_values(v, probe)):
        return False, "profil vidéo non pris en charge"
    if audio:
        secondary = audio["index"] != first_audio(probe)
        if not codec_profiles_ok(profile, "VideoAudio", acodec, container, audio_values(audio, secondary)):
            return False, "piste audio non prise en charge"
    return True, ""


def transcoding_profile(profile):
    """Le profil de conversion « Streaming » de l'appareil (HLS de préférence)."""
    best = None
    for tp in profile.get("TranscodingProfiles") or []:
        if not isinstance(tp, dict) or tp.get("Type", "Video") != "Video":
            continue
        if str(tp.get("Context") or "Streaming") != "Streaming":
            continue
        if str(tp.get("Protocol") or "").lower() == "hls":
            return tp
        best = best or tp
    return best or {}


def stream_caps(profile, probe, audio, max_channels=None, allow_video_copy=True, allow_audio_copy=True):
    """Capacités au format du planificateur de Séance (transcoder.plan) pour un flux HLS."""
    tp = transcoding_profile(profile)
    v = probe.get("video") or {}
    caps = {"video": set(), "audio": set(), "containers": set(), "max_channels": 2}
    vcodecs = split(tp.get("VideoCodec")) or ["h264"]
    codec = v.get("codec") or ""
    if allow_video_copy and v and codec in vcodecs and \
            codec_profiles_ok(profile, "Video", codec, "mp4", video_values(v, probe)) and \
            conditions_ok(tp.get("Conditions"), video_values(v, probe)):
        depth = bit_depth(v)
        token = {"h264": "h264", "hevc": "hevc10" if depth == 10 else "hevc",
                 "av1": "av1_10" if depth == 10 else "av1", "vp9": "vp9"}.get(codec)
        if token:
            caps["video"].add(token)
    try:
        channels = int(tp.get("MaxAudioChannels") or 0)
    except (TypeError, ValueError):
        channels = 0
    if max_channels:
        channels = min(channels, max_channels) if channels else max_channels
    caps["max_channels"] = max(2, channels or 2)
    if allow_audio_copy and audio:
        acodec = audio.get("codec") or ""
        if acodec in (split(tp.get("AudioCodec")) or ["aac"]) and acodec in ("aac", "mp3", "ac3", "eac3", "flac", "opus") \
                and codec_profiles_ok(profile, "VideoAudio", acodec, "mp4", audio_values(audio)):
            caps["audio"].add(acodec)
    return caps


def quality_for(max_bitrate, probe, qualities):
    """Qualité Séance la plus haute qui tient dans le débit maximal demandé par l'appareil."""
    bitrate = probe.get("bitrate") or 0
    if not max_bitrate or not bitrate or bitrate <= max_bitrate:
        return "original"
    for name in ("1080p", "720p", "480p"):
        if qualities[name][1] * 1000 <= max_bitrate:
            return name
    return "480p"


def subtitle_method(profile, fmt, playing="direct", container=""):
    """Comment l'appareil reçoit une piste de sous-titres : Embed, External, Hls ou Encode (incrustation)."""
    profiles = [p for p in profile.get("SubtitleProfiles") or [] if isinstance(p, dict)]

    def find(method, formats):
        for p in profiles:
            if str(p.get("Method")) != method:
                continue
            if not contains(p.get("Container"), container) and p.get("Container"):
                continue
            f = str(p.get("Format") or "").lower()
            if f in formats:
                return f
        return None
    if playing == "direct" and find("Embed", {fmt}):
        return "Embed", fmt
    if find("External", {fmt}):
        return "External", fmt
    if fmt in TEXT_FORMATS:
        # Séance convertit tout sous-titre texte en WebVTT / SRT
        for target in ("vtt", "srt", "ass", "ssa"):
            if find("External", {target}):
                return "External", target
        if not profiles:
            return "External", "vtt"
    return "Encode", fmt
