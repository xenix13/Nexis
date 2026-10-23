"""Objets « BaseItemDto » de Jellyfin construits depuis le catalogue de Séance."""
import json
import os

from .. import media
from ..catalog import EXTERNAL_BASE, LANG_NAMES, CODEC_NAMES
from .common import (EXTRA_TYPES, K_GENRE, K_ITEM, K_LIB, K_PERSON, K_STUDIO, LANG2, TICKS, TYPES, date_iso,
                     guid, iso, people_of)
from .profile import SUB_FORMATS, TEXT_FORMATS, bit_depth, container_name, range_type

POSTER_RATIO = 2 / 3
WIDE_RATIO = 16 / 9


def probe_of(row):
    try:
        return json.loads(row.get("probe") or "{}")
    except (TypeError, ValueError):
        return {}


def lang_name(code):
    return LANG_NAMES.get(code or "", (code or "").upper() or "Inconnu")


class Builder:
    """Fabrique les objets d'éléments pour un utilisateur (avec son état de lecture)."""

    def __init__(self, jf, user, fields=()):
        self.jf = jf
        self.db = jf.db
        self.user = user
        self.fields = {f.lower() for f in fields}
        self.shows = {}
        self.seasons = {}

    def want(self, name):
        return name.lower() in self.fields

    # ------------------------------------------------------------ état de lecture
    def user_data_many(self, rows):
        uid = self.user["id"]
        ids = [str(r["id"]) for r in rows]
        state, counts, favs = {}, {}, set()
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            marks = ",".join("?" * len(chunk))
            for p in self.db.all(f"SELECT rating_key, position_ms, duration_ms, watched, view_count, last_watched_at, "
                                 f"updated_at FROM progress WHERE user_id=? AND rating_key IN ({marks})", [uid, *chunk]):
                state[p["rating_key"]] = p
            for col in ("show_key", "season_key"):
                for p in self.db.all(f"SELECT {col} AS k, COUNT(*) AS n FROM progress WHERE user_id=? AND watched=1 "
                                     f"AND {col} IN ({marks}) GROUP BY {col}", [uid, *chunk]):
                    counts[p["k"]] = counts.get(p["k"], 0) + p["n"]
            ints = [int(c) for c in chunk]
            for f in self.db.all(f"SELECT item_id FROM watchlist WHERE user_id=? AND item_id IN ({marks})", [uid, *ints]):
                favs.add(str(f["item_id"]))
        out = {}
        for r in rows:
            k = str(r["id"])
            d = {"PlaybackPositionTicks": 0, "PlayCount": 0, "IsFavorite": k in favs, "Played": False,
                 "Key": k, "ItemId": guid(K_ITEM, r["id"])}
            if r["type"] in ("show", "season"):
                total = r.get("leaves") or 0
                seen = counts.get(k, 0)
                d["UnplayedItemCount"] = max(0, total - seen)
                d["Played"] = bool(total) and seen >= total
                d["PlayedPercentage"] = round(100 * seen / total, 1) if total else 0
            else:
                p = state.get(k)
                if p:
                    d["Played"] = bool(p["watched"])
                    d["PlayCount"] = p["view_count"] or (1 if p["watched"] else 0)
                    d["PlaybackPositionTicks"] = 0 if p["watched"] else (p["position_ms"] or 0) * TICKS
                    dur = p["duration_ms"] or r.get("duration_ms") or 0
                    if d["PlaybackPositionTicks"] and dur:
                        d["PlayedPercentage"] = round(100 * (p["position_ms"] or 0) / dur, 2)
                    if p["last_watched_at"]:
                        d["LastPlayedDate"] = iso(p["last_watched_at"])
            out[k] = d
        return out

    # ------------------------------------------------------------ parents (séries, saisons)
    def show(self, show_id):
        if show_id not in self.shows:
            self.shows[show_id] = self.db.one("SELECT id, title, img_version, library_id FROM items WHERE id=?",
                                              (show_id,))
        return self.shows[show_id]

    def season(self, season_id):
        if season_id not in self.seasons:
            self.seasons[season_id] = self.db.one("SELECT id, title, idx, img_version FROM items WHERE id=?",
                                                  (season_id,))
        return self.seasons[season_id]

    # ------------------------------------------------------------ éléments
    def many(self, rows):
        ud = self.user_data_many(rows)
        return [self.item(r, ud.get(str(r["id"]))) for r in rows]

    def one(self, row):
        return self.many([row])[0]

    def item(self, row, user_data=None):
        jf = self.jf
        t = row["type"]
        rid = row["id"]
        v = row.get("img_version") or 1
        d = {
            "Name": row["title"], "ServerId": jf.server_id(), "Id": guid(K_ITEM, rid), "Etag": f"{rid:x}{v:x}",
            "DateCreated": iso(row.get("added_at")), "CanDelete": False, "CanDownload": False,
            "SortName": row.get("sort_title") or row["title"], "PremiereDate": date_iso(row.get("released")),
            "ExternalUrls": [], "OfficialRating": row.get("content_rating"), "CommunityRating": row.get("rating"),
            "ProductionYear": row.get("year"), "IsFolder": t in ("show", "season"), "Type": TYPES[t],
            "LocationType": "FileSystem", "MediaType": "Video" if t in ("movie", "episode", "extra") else "Unknown",
            "LocalTrailerCount": 0, "ImageBlurHashes": {}, "UserData": user_data or {
                "PlaybackPositionTicks": 0, "PlayCount": 0, "IsFavorite": False, "Played": False, "Key": str(rid),
                "ItemId": guid(K_ITEM, rid)},
        }
        if row.get("original_title") and row["original_title"] != row["title"]:
            d["OriginalTitle"] = row["original_title"]
        if "summary" in row:
            d["Overview"] = row.get("summary") or ""
        if row.get("tagline"):
            d["Taglines"] = [row["tagline"]]
        if t in ("movie", "episode", "extra"):
            d["RunTimeTicks"] = (row.get("duration_ms") or 0) * TICKS
            d["VideoType"] = "VideoFile"
        genres = []
        if row.get("genres"):
            try:
                genres = [g for g in json.loads(row["genres"]) if isinstance(g, str)]
            except ValueError:
                genres = []
        d["Genres"] = genres
        d["GenreItems"] = [{"Name": g, "Id": jf.remember(K_GENRE, g)} for g in genres]
        if row.get("studio"):
            d["Studios"] = [{"Name": row["studio"], "Id": jf.remember(K_STUDIO, row["studio"])}]
        else:
            d["Studios"] = []
        d["ProviderIds"] = {}
        if t in ("movie", "show"):
            if row.get("tmdb_id"):
                d["ProviderIds"]["Tmdb"] = str(row["tmdb_id"])
            if row.get("imdb_id"):
                d["ProviderIds"]["Imdb"] = row["imdb_id"]
            if row.get("tvdb_id"):
                d["ProviderIds"]["Tvdb"] = str(row["tvdb_id"])
        if "people" in row and (self.want("People") or self.want("_detail")):
            d["People"] = [{"Name": n, "Id": jf.remember(K_PERSON, n), "Role": role, "Type": kind}
                           for n, role, kind in people_of(row)[:40]]
        # images
        tag = jf.image_tag(rid, "thumb", v)
        art = jf.image_tag(rid, "art", v)
        d["ImageTags"] = {"Primary": tag}
        d["BackdropImageTags"] = []
        d["PrimaryImageAspectRatio"] = WIDE_RATIO if t in ("episode", "extra") else POSTER_RATIO
        if t in ("movie", "show"):
            d["BackdropImageTags"] = [art]
            d["ImageTags"]["Thumb"] = art
        if t == "show":
            d["ChildCount"] = row.get("child_count") or 0
            d["RecursiveItemCount"] = row.get("leaves") or 0
            d["AirDays"] = []
            d["Status"] = None
            d["ParentId"] = guid(K_LIB, row["library_id"])
        elif t == "movie":
            d["ParentId"] = guid(K_LIB, row["library_id"])
        elif t == "season":
            show = self.show(row["show_id"])
            d.update(IndexNumber=row.get("idx"), SeriesId=guid(K_ITEM, row["show_id"]), ChildCount=row.get("leaves") or 0,
                     RecursiveItemCount=row.get("leaves") or 0, ParentId=guid(K_ITEM, row["show_id"]))
            if show:
                sv = show["img_version"]
                d.update(SeriesName=show["title"], SeriesPrimaryImageTag=jf.image_tag(show["id"], "thumb", sv),
                         ParentBackdropItemId=guid(K_ITEM, show["id"]),
                         ParentBackdropImageTags=[jf.image_tag(show["id"], "art", sv)],
                         ParentThumbItemId=guid(K_ITEM, show["id"]), ParentThumbImageTag=jf.image_tag(show["id"], "art", sv))
        elif t == "episode":
            show = self.show(row["show_id"])
            season = self.season(row["parent_id"])
            d.update(IndexNumber=row.get("idx"), ParentIndexNumber=season["idx"] if season else row.get("parent_idx"),
                     SeriesId=guid(K_ITEM, row["show_id"]), SeasonId=guid(K_ITEM, row["parent_id"]),
                     ParentId=guid(K_ITEM, row["parent_id"]), SeasonName=season["title"] if season else "")
            if show:
                sv = show["img_version"]
                d.update(SeriesName=show["title"], SeriesPrimaryImageTag=jf.image_tag(show["id"], "thumb", sv),
                         ParentBackdropItemId=guid(K_ITEM, show["id"]),
                         ParentBackdropImageTags=[jf.image_tag(show["id"], "art", sv)],
                         ParentThumbItemId=guid(K_ITEM, show["id"]), ParentThumbImageTag=jf.image_tag(show["id"], "art", sv),
                         ParentLogoItemId=None)
        elif t == "extra":
            d["ExtraType"] = EXTRA_TYPES.get(row.get("extra_kind") or "other", "Clip")
            d["ParentId"] = guid(K_ITEM, row["parent_id"]) if row.get("parent_id") else None
        if t in ("movie", "episode", "extra") and "probe" in row:
            probe = probe_of(row)
            v_ = probe.get("video") or {}
            d["HasSubtitles"] = bool(probe.get("subs") or probe.get("external"))
            d["Container"] = container_name(probe, row.get("key") or "")
            if v_:
                d.update(Width=v_.get("width"), Height=v_.get("height"), IsHD=(v_.get("height") or 0) >= 720)
            if self.want("MediaSources") or self.want("MediaStreams") or self.want("_detail"):
                ms = self.media_source(row, probe)
                d["MediaSources"] = [ms]
                d["MediaStreams"] = ms["MediaStreams"]
            if self.want("Chapters") or self.want("_detail"):
                d["Chapters"] = self.chapters(row, probe)
            if self.want("Trickplay") or self.want("_detail"):
                d["Trickplay"] = self.trickplay(row)
        return d

    def chapters(self, row, probe):
        chapters = [c for c in probe.get("chapters") or [] if (c.get("end") or 0) > (c.get("start") or 0)]
        out = []
        for n, c in enumerate(chapters):
            ch = {"StartPositionTicks": int(c["start"] * 1000) * TICKS, "Name": c.get("title") or f"Chapitre {n + 1}",
                  "ImageDateModified": iso(row.get("updated_at") or row.get("added_at"))}
            if len(chapters) >= 2:
                ch["ImageTag"] = self.jf.image_tag(row["id"], f"chapter{n}", row.get("img_version") or 1)
            out.append(ch)
        return out

    def trickplay(self, row):
        index = self.jf.ctx.subtitles.trick_index(row)
        if not index:
            return {}
        return {guid(K_ITEM, row["id"]): {str(index["w"]): {
            "Width": index["w"], "Height": index["h"], "TileWidth": index["cols"], "TileHeight": index["rows"],
            "ThumbnailCount": index["count"], "Interval": int(index["interval"] * 1000),
            "Bandwidth": 0}}}

    # ------------------------------------------------------------ source (fichier) et pistes
    def media_source(self, row, probe=None, token=None):
        probe = probe if probe is not None else probe_of(row)
        path = row.get("key") or ""
        streams = []
        v = probe.get("video")
        if v:
            depth = bit_depth(v)
            rng = range_type(v)
            h = v.get("height") or 0
            label = "4K" if h > 1100 else f"{h}p" if h else ""
            streams.append({
                "Codec": v.get("codec") or "", "Profile": v.get("profile") or None, "Level": v.get("level") or 0,
                "Type": "Video", "Index": v.get("index", 0), "Width": v.get("width"), "Height": h,
                "BitDepth": depth, "BitRate": v.get("bitrate") or probe.get("bitrate") or None,
                "AverageFrameRate": v.get("fps") or None, "RealFrameRate": v.get("fps") or None,
                "PixelFormat": v.get("pix_fmt") or None, "RefFrames": v.get("refs"),
                "VideoRange": "HDR" if rng != "SDR" else "SDR", "VideoRangeType": rng,
                "DisplayTitle": " ".join(p for p in (label, str(v.get("codec") or "").upper(), rng) if p),
                "IsInterlaced": bool(v.get("interlaced")), "IsAVC": v.get("codec") == "h264",
                "IsDefault": True, "IsForced": False, "IsHearingImpaired": False, "IsExternal": False,
                "IsTextSubtitleStream": False, "SupportsExternalStream": False, "AspectRatio": None,
                "TimeBase": None, "Language": None})
        for a in probe.get("audio") or []:
            ch = a.get("channels") or 2
            layout = "Stéréo" if ch == 2 else "Mono" if ch == 1 else f"{ch - 1}.1"
            title = f"{lang_name(a.get('lang'))} ({CODEC_NAMES.get(a.get('codec'), str(a.get('codec')).upper())} {layout})"
            if a.get("title"):
                title += f" — {a['title']}"
            streams.append({
                "Codec": a.get("codec") or "", "Language": a.get("lang") or None, "Title": a.get("title") or None,
                "DisplayTitle": title, "Type": "Audio", "Index": a["index"], "Channels": ch,
                "ChannelLayout": a.get("layout") or None, "SampleRate": a.get("rate") or None,
                "IsDefault": bool(a.get("default")), "IsForced": False, "IsHearingImpaired": False,
                "IsInterlaced": False, "IsExternal": False, "IsTextSubtitleStream": False,
                "SupportsExternalStream": False})
        for s in probe.get("subs") or []:
            codec = s.get("codec") or ""
            if codec not in media.TEXT_SUB_CODECS and codec not in media.IMAGE_SUB_CODECS:
                continue
            fmt = SUB_FORMATS.get(codec, codec)
            streams.append(self.sub_stream(row, s["index"], fmt, s, False))
        for n, e in enumerate(probe.get("external") or []):
            fmt = SUB_FORMATS.get(e.get("codec"), e.get("codec") or "srt")
            streams.append(self.sub_stream(row, EXTERNAL_BASE + n, fmt, e, True))
        container = container_name(probe, path)
        return {
            "Protocol": "File", "Id": guid(K_ITEM, row["id"]), "Path": os.path.basename(path),
            "Type": "Default", "Container": container, "Size": row.get("file_size"),
            "Name": row["title"], "IsRemote": False, "ETag": None,
            "RunTimeTicks": (row.get("duration_ms") or 0) * TICKS, "ReadAtNativeFramerate": False,
            "IgnoreDts": False, "IgnoreIndex": False, "GenPtsInput": False, "SupportsTranscoding": True,
            "SupportsDirectStream": True, "SupportsDirectPlay": True, "IsInfiniteStream": False,
            "UseMostCompatibleTranscodingProfile": False, "RequiresOpening": False, "RequiresClosing": False,
            "RequiresLooping": False, "SupportsProbing": True, "VideoType": "VideoFile",
            "MediaStreams": streams, "MediaAttachments": [], "Formats": [], "Bitrate": probe.get("bitrate") or None,
            "RequiredHttpHeaders": {}, "TranscodingSubProtocol": "http", "HasSegments": True,
            "DefaultAudioStreamIndex": (probe.get("audio") or [{}])[0].get("index"),
        }

    def sub_stream(self, row, index, fmt, s, external):
        bits = [CODEC_NAMES.get(s.get("codec"), str(s.get("codec") or "").upper())]
        if s.get("forced"):
            bits.append("forcés")
        if external:
            bits.append("externe")
        title = f"{lang_name(s.get('lang'))} ({', '.join(bits)})"
        if s.get("title"):
            title += f" — {s['title']}"
        lang = s.get("lang") or None
        return {
            "Codec": "PGSSUB" if fmt == "pgssub" else fmt, "Language": lang, "Title": s.get("title") or None,
            "DisplayTitle": title, "LocalizedForced": "Forcés", "LocalizedExternal": "Externe",
            "Type": "Subtitle", "Index": index, "IsDefault": bool(s.get("default")), "IsForced": bool(s.get("forced")),
            "IsHearingImpaired": bool(s.get("sdh")), "IsInterlaced": False, "IsExternal": external,
            "IsTextSubtitleStream": fmt in TEXT_FORMATS, "SupportsExternalStream": True,
            "Path": None, "DeliveryMethod": "External", "IsExternalUrl": False,
        }


def lang2(code):
    return LANG2.get(code or "", code or "")
