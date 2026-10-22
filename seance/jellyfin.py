"""API compatible Jellyfin (sous-ensemble) : permet à Seerr / Jellyseerr d'utiliser Séance comme serveur.

Seerr se connecte « comme à un Jellyfin » : authentification avec un compte Séance, création d'une clé
d'API, puis lecture des bibliothèques pour savoir ce qui est déjà disponible (identifiants TMDB/IMDb/TVDB,
saisons, épisodes, résolution). Seuls les points d'entrée utilisés par Seerr sont fournis, en lecture
seule ; la lecture vidéo reste réservée à l'interface de Séance.

Identifiants : Jellyfin utilise des GUID (32 caractères hexadécimaux). Séance les dérive de ses propres
numéros : 5eace0 + type (2) + numéro (24), ce qui les rend stables et réversibles.
"""
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone

from .auth import check_credentials
from .web import HTTPError, Response

JF_VERSION = "10.10.7"
PREFIX = "5eace0"
K_USER, K_LIB, K_ITEM = 1, 2, 3
TYPES = {"movie": "Movie", "show": "Series", "season": "Season", "episode": "Episode"}
AUTH_RE = re.compile(r'(\w+)="([^"]*)"')


def guid(kind, n):
    return f"{PREFIX}{kind:02x}{int(n):024x}"


def unguid(value, kind):
    v = (value or "").replace("-", "").lower()
    if len(v) != 32 or not v.startswith(PREFIX) or not re.fullmatch(r"[0-9a-f]{32}", v):
        return None
    if int(v[6:8], 16) != kind:
        return None
    return int(v[8:], 16)


def iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def register(app, ctx, x):
    db = ctx.db

    def enabled():
        return db.setting("jellyfin_api", "0") == "1"

    def server_id():
        sid = db.setting("jf_server_id")
        if not sid:
            sid = secrets.token_hex(16)
            db.set_setting("jf_server_id", sid)
        return sid

    def server_name():
        return db.setting("server_name") or "Séance"

    # ------------------------------------------------------------ authentification
    def auth_fields(req):
        raw = req.headers.get("Authorization") or req.headers.get("X-Emby-Authorization") or ""
        fields = dict(AUTH_RE.findall(raw)) if raw.lower().startswith(("mediabrowser", "emby")) else {}
        token = (fields.get("Token") or req.headers.get("X-Emby-Token") or req.headers.get("X-MediaBrowser-Token")
                 or req.arg("api_key") or req.arg("ApiKey") or "")
        return fields, token.strip()

    def require_enabled():
        if not enabled():
            raise HTTPError(404, "Introuvable")

    def token_user(req):
        require_enabled()
        _, token = auth_fields(req)
        if not token or len(token) > 200:
            raise HTTPError(401, "Unauthorized")
        h = _hash(token)
        row = db.one("SELECT u.*, t.kind AS t_kind, t.last_used AS t_used FROM jf_tokens t JOIN users u ON u.id=t.user_id "
                     "WHERE t.token_hash=?", (h,))
        if not row or row["disabled"]:
            raise HTTPError(401, "Unauthorized")
        now = int(time.time())
        if now - row["t_used"] > 300:
            db.run("UPDATE jf_tokens SET last_used=? WHERE token_hash=?", (now, h))
        row.pop("password_hash", None)
        return row

    def require_admin(user):
        if not user["is_admin"]:
            raise HTTPError(403, "Forbidden")

    def user_dto(u):
        libs = sorted(x.allowed_set(u), key=int)
        return {
            "Name": u["username"], "ServerId": server_id(), "Id": guid(K_USER, u["id"]),
            "HasPassword": True, "HasConfiguredPassword": True, "HasConfiguredEasyPassword": False,
            "EnableAutoLogin": False,
            "Configuration": {"GroupedFolders": [], "PlayDefaultAudioTrack": True, "SubtitleMode": "Default",
                              "OrderedViews": [], "LatestItemsExcludes": [], "MyMediaExcludes": [],
                              "HidePlayedInLatest": True, "RememberAudioSelections": True,
                              "RememberSubtitleSelections": True, "EnableNextEpisodeAutoPlay": True},
            "Policy": {"IsAdministrator": bool(u["is_admin"]), "IsHidden": False, "IsDisabled": bool(u["disabled"]),
                       "EnableAllFolders": bool(u["is_admin"] or u["all_libraries"]),
                       "EnabledFolders": [guid(K_LIB, i) for i in libs],
                       "EnableMediaPlayback": True, "EnableRemoteAccess": True,
                       "AuthenticationProviderId": "Jellyfin.Server.Implementations.Users.DefaultAuthenticationProvider",
                       "PasswordResetProviderId": "Jellyfin.Server.Implementations.Users.DefaultPasswordResetProvider"},
        }

    def issue(user_id, kind, app_name, device, ip):
        token = secrets.token_hex(16)
        now = int(time.time())
        db.run("INSERT INTO jf_tokens(token_hash,kind,secret,user_id,app,device,ip,created_at,last_used) "
               "VALUES(?,?,?,?,?,?,?,?,?)",
               (_hash(token), kind, token if kind == "apikey" else None, user_id, app_name[:60], device[:60],
                ip, now, now))
        return token

    # ------------------------------------------------------------ système
    def info(public):
        d = {"LocalAddress": "", "ServerName": server_name(), "Version": JF_VERSION,
             "ProductName": "Jellyfin Server", "OperatingSystem": "Linux", "Id": server_id(),
             "StartupWizardCompleted": True}
        if not public:
            d.update({"OperatingSystemDisplayName": "Linux", "HasPendingRestart": False, "IsShuttingDown": False,
                      "SupportsLibraryMonitor": True, "WebSocketPortNumber": 0, "CanSelfRestart": False,
                      "CanLaunchWebBrowser": False, "HasUpdateAvailable": False, "TranscodingTempPath": "",
                      "CachePath": "", "LogPath": "", "InternalMetadataPath": "", "ProgramDataPath": "",
                      "ItemsByNamePath": "", "WebPath": "", "EncoderLocation": "System",
                      "SystemArchitecture": "X64", "PackageName": "seance", "CompletedInstallations": []})
        return d

    @app.compat_route("GET", "/System/Info/Public")
    def jf_info_public(req):
        require_enabled()
        return info(True)

    @app.compat_route("GET", "/System/Info")
    def jf_info(req):
        token_user(req)
        return info(False)

    @app.compat_route("GET", "/System/Ping")
    def jf_ping(req):
        require_enabled()
        return Response(json.dumps("Jellyfin Server").encode(), 200, "application/json; charset=utf-8")

    @app.compat_route("GET", "/QuickConnect/Enabled")
    def jf_qc_enabled(req):
        require_enabled()
        return False

    @app.compat_route("POST", "/QuickConnect/Initiate")
    def jf_qc_initiate(req):
        require_enabled()
        raise HTTPError(401, "Quick connect is disabled")

    @app.compat_route("POST", "/Users/AuthenticateByName")
    def jf_login(req):
        require_enabled()
        b = req.json()
        username = str(b.get("Username") or b.get("username") or "").strip()[:64]
        password = str(b.get("Pw") or b.get("pw") or b.get("Password") or "")[:256]
        keys = ("ip:" + req.client_ip, "u:" + username.lower())
        ctx.throttle.check(*keys)
        user = check_credentials(db, username, password) if username and password else None
        if not user or user.get("managed"):
            ctx.throttle.fail(*keys)
            raise HTTPError(401, "Invalid username or password")
        ctx.throttle.reset(keys[1])
        fields, _ = auth_fields(req)
        token = issue(user["id"], "session", fields.get("Client") or "Client", fields.get("Device") or "",
                      req.client_ip)
        user.pop("password_hash", None)
        return {"User": user_dto(user), "AccessToken": token, "ServerId": server_id(),
                "SessionInfo": {"Id": secrets.token_hex(16), "UserId": guid(K_USER, user["id"]),
                                "UserName": user["username"], "Client": fields.get("Client") or "",
                                "DeviceName": fields.get("Device") or "", "DeviceId": fields.get("DeviceId") or "",
                                "ApplicationVersion": fields.get("Version") or "", "IsActive": True,
                                "ServerId": server_id(), "LastActivityDate": iso(time.time())}}

    @app.compat_route("POST", "/Sessions/Logout")
    def jf_logout(req):
        require_enabled()
        _, token = auth_fields(req)
        if token:
            db.run("DELETE FROM jf_tokens WHERE token_hash=? AND kind='session'", (_hash(token),))
        return Response(b"", 204)

    # -- clés d'API (Seerr en crée une avec le compte administrateur)
    @app.compat_route("POST", "/Auth/Keys")
    def jf_key_create(req):
        u = token_user(req)
        require_admin(u)
        name = re.sub(r"[^\w .-]", "", str(req.arg("App") or req.arg("app") or "Application"))[:60] or "Application"
        issue(u["id"], "apikey", name, "", req.client_ip)
        return Response(b"", 204)

    @app.compat_route("GET", "/Auth/Keys")
    def jf_key_list(req):
        u = token_user(req)
        require_admin(u)
        rows = db.all("SELECT secret, app, created_at FROM jf_tokens WHERE kind='apikey' ORDER BY created_at, rowid")
        items = [{"Id": n + 1, "AccessToken": r["secret"], "AppName": r["app"], "DateCreated": iso(r["created_at"])}
                 for n, r in enumerate(rows)]
        return {"Items": items, "TotalRecordCount": len(items), "StartIndex": 0}

    @app.compat_route("DELETE", "/Auth/Keys/(?P<key>[0-9a-fA-F]{32})")
    def jf_key_delete(req):
        u = token_user(req)
        require_admin(u)
        db.run("DELETE FROM jf_tokens WHERE kind='apikey' AND token_hash=?", (_hash(req.params["key"].lower()),))
        return Response(b"", 204)

    # ------------------------------------------------------------ utilisateurs
    def target_user(req, uid):
        u = token_user(req)
        if uid.lower() == "me":
            return u, u
        n = unguid(uid, K_USER)
        if n is None:
            raise HTTPError(404, "User not found")
        if n != u["id"]:
            require_admin(u)
        t = db.one("SELECT * FROM users WHERE id=?", (n,))
        if not t:
            raise HTTPError(404, "User not found")
        t.pop("password_hash", None)
        return u, t

    @app.compat_route("GET", "/Users")
    def jf_users(req):
        u = token_user(req)
        if not u["is_admin"]:
            return [user_dto(u)]
        rows = db.all("SELECT * FROM users WHERE managed=0 OR managed IS NULL ORDER BY id")
        return [user_dto(r) for r in rows]

    @app.compat_route("GET", "/Users/(?P<uid>Me|[0-9a-fA-F-]{32,36})")
    def jf_user(req):
        return user_dto(target_user(req, req.params["uid"])[1])

    @app.compat_route("GET", "/Users/(?P<uid>[0-9a-fA-F-]{32,36})/Images/Primary")
    def jf_user_image(req):
        require_enabled()
        raise HTTPError(404, "No image")

    # ------------------------------------------------------------ bibliothèques
    def lib_dto(lib):
        return {"Name": lib["title"], "ServerId": server_id(), "Id": guid(K_LIB, lib["id"]),
                "Type": "CollectionFolder", "CollectionType": "movies" if lib["type"] == "movie" else "tvshows",
                "IsFolder": True, "LocationType": "FileSystem", "Etag": lib["id"]}

    def libs_for(user):
        allowed = x.allowed_set(user)
        return [l for l in x.libraries_all() if l["id"] in allowed]

    def folders(user):
        items = [lib_dto(l) for l in libs_for(user)]
        return {"Items": items, "TotalRecordCount": len(items), "StartIndex": 0}

    @app.compat_route("GET", "/Library/MediaFolders")
    def jf_media_folders(req):
        u = token_user(req)
        require_admin(u)
        return folders(u)

    @app.compat_route("GET", "/Users/(?P<uid>Me|[0-9a-fA-F-]{32,36})/Views")
    def jf_views(req):
        return folders(target_user(req, req.params["uid"])[1])

    # ------------------------------------------------------------ éléments
    def probe_of(row):
        try:
            return json.loads(row["probe"] or "{}")
        except ValueError:
            return {}

    def media_sources(row):
        p = probe_of(row)
        streams = []
        v = p.get("video")
        if v:
            streams.append({"Type": "Video", "Codec": v.get("codec") or "", "Width": v.get("width") or 0,
                            "Height": v.get("height") or 0, "Index": v.get("index", 0),
                            "DisplayTitle": f"{v.get('height') or ''}p {str(v.get('codec') or '').upper()}".strip()})
        for a in p.get("audio", []):
            streams.append({"Type": "Audio", "Codec": a.get("codec") or "", "Language": a.get("lang") or None,
                            "Channels": a.get("channels"), "Index": a.get("index"),
                            "DisplayTitle": a.get("title") or (a.get("lang") or "Audio")})
        for s in p.get("subs", []):
            streams.append({"Type": "Subtitle", "Codec": s.get("codec") or "", "Language": s.get("lang") or None,
                            "Index": s.get("index"), "IsForced": bool(s.get("forced")),
                            "DisplayTitle": s.get("title") or (s.get("lang") or "Sous-titres")})
        return [{"Protocol": "File", "Id": guid(K_ITEM, row["id"]), "Path": os.path.basename(row["key"] or ""),
                 "Type": "Default", "Container": (p.get("container") or "").split(",")[0], "Size": row["file_size"],
                 "Name": row["title"], "VideoType": "VideoFile", "IsRemote": False, "SupportsDirectPlay": True,
                 "RunTimeTicks": (row["duration_ms"] or 0) * 10000, "MediaStreams": streams}]

    def name_of(item_id, cache):
        if item_id not in cache:
            cache[item_id] = db.one("SELECT title, idx FROM items WHERE id=?", (item_id,))
        return cache[item_id]

    def item_dto(row, fields=(), cache=None):
        cache = {} if cache is None else cache
        t = row["type"]
        d = {"Name": row["title"], "ServerId": server_id(), "Id": guid(K_ITEM, row["id"]), "Type": TYPES[t],
             "IsFolder": t in ("show", "season"), "LocationType": "FileSystem",
             "MediaType": "Video" if t in ("movie", "episode") else "Unknown",
             "ProductionYear": row["year"], "PremiereDate": f"{row['released']}T00:00:00.0000000Z" if row["released"] else None,
             "DateCreated": iso(row["added_at"]), "CommunityRating": row["rating"],
             "OfficialRating": row["content_rating"], "Overview": row["summary"],
             "RunTimeTicks": (row["duration_ms"] or 0) * 10000 if t in ("movie", "episode") else None,
             "ParentId": guid(K_ITEM, row["parent_id"]) if row["parent_id"] else guid(K_LIB, row["library_id"]),
             "HasSubtitles": bool(probe_of(row).get("subs")) if t in ("movie", "episode") else False,
             "ProviderIds": {}, "UserData": {}}
        if t in ("movie", "show"):
            ids = {}
            if row["tmdb_id"]:
                ids["Tmdb"] = str(row["tmdb_id"])
            if row.get("imdb_id"):
                ids["Imdb"] = row["imdb_id"]
            if row.get("tvdb_id"):
                ids["Tvdb"] = str(row["tvdb_id"])
            d["ProviderIds"] = ids
        if t == "season":
            show = name_of(row["show_id"], cache)
            d.update(IndexNumber=row["idx"], SeriesId=guid(K_ITEM, row["show_id"]),
                     SeriesName=show["title"] if show else "")
        if t == "episode":
            show, season = name_of(row["show_id"], cache), name_of(row["parent_id"], cache)
            d.update(IndexNumber=row["idx"], ParentIndexNumber=season["idx"] if season else None,
                     SeriesId=guid(K_ITEM, row["show_id"]), SeasonId=guid(K_ITEM, row["parent_id"]),
                     SeriesName=show["title"] if show else "", SeasonName=season["title"] if season else "")
        if t in ("movie", "episode") and "mediasources" in fields:
            ms = media_sources(row)
            d["MediaSources"] = ms
            v = next((s for s in ms[0]["MediaStreams"] if s["Type"] == "Video"), None)
            if v:
                d.update(Width=v["Width"], Height=v["Height"], IsHD=(v["Height"] or 0) >= 720)
        if t == "show":
            d["ChildCount"] = db.scalar("SELECT COUNT(*) FROM items WHERE parent_id=? AND type='season'", (row["id"],))
        return d

    def fields_of(req):
        return {f.strip().lower() for f in (req.arg("fields") or req.arg("Fields") or "").split(",") if f.strip()}

    def visible_row(user, row):
        return row and str(row["library_id"]) in x.allowed_set(user) and x.visible(user, row) \
            and row["type"] in TYPES

    def result(items, start=0, total=None):
        return {"Items": items, "TotalRecordCount": len(items) if total is None else total, "StartIndex": start}

    def arg(req, *names_, default=None):
        for n in names_:
            v = req.arg(n)
            if v not in (None, ""):
                return v
        return default

    def item_by_guid(user, value):
        n = unguid(value, K_ITEM)
        if n is None:
            return None
        row = db.one(x.ITEM_SQL + "WHERE i.id=?", (n,))
        return row if visible_row(user, row) else None

    def query_items(req, user):
        cache = {}
        fields = fields_of(req)
        ids = arg(req, "ids", "Ids")
        if ids:
            rows = [item_by_guid(user, v) for v in ids.split(",")[:200]]
            return result([item_dto(r, fields, cache) for r in rows if r])
        start = max(0, int(arg(req, "StartIndex", "startIndex", default="0") or 0))
        limit = int(arg(req, "Limit", "limit", default="0") or 0)
        types = {t.strip().lower() for t in (arg(req, "IncludeItemTypes", "includeItemTypes", default="") or "").split(",")
                 if t.strip()}
        wanted = [k for k, v in TYPES.items() if v.lower() in types] if types else None
        parent = arg(req, "ParentId", "parentId")
        recursive = (arg(req, "Recursive", "recursive", default="false") or "").lower() == "true"
        where, args = ["i.type IN ('movie','show','season','episode')"], []
        allowed = sorted(x.allowed_set(user), key=int)
        where.append(f"i.library_id IN ({','.join('?' * len(allowed)) or 'NULL'})")
        args += [int(a) for a in allowed]
        if parent:
            lib = unguid(parent, K_LIB)
            pid = unguid(parent, K_ITEM)
            if lib is not None:
                where.append("i.library_id=?")
                args.append(lib)
                if not recursive:
                    where.append("i.type IN ('movie','show')")
            elif pid is not None:
                where.append("(i.parent_id=? OR i.show_id=?)" if recursive else "i.parent_id=?")
                args += [pid, pid] if recursive else [pid]
            else:
                return result([])
        if wanted is not None:
            if not wanted:
                return result([])
            where.append(f"i.type IN ({','.join('?' * len(wanted))})")
            args += wanted
        term = arg(req, "SearchTerm", "searchTerm")
        if term:
            from .names import fold  # noqa: E402
            where.append("i.search LIKE ?")
            args.append(f"%{fold(term)[:100]}%")
        age_sql, age_args = x.age_filter(user)
        sql = x.ITEM_SQL + "WHERE " + " AND ".join(where) + age_sql
        sort = (arg(req, "SortBy", "sortBy", default="SortName") or "").split(",")[0].lower()
        order = {"datecreated": "i.added_at", "premieredate": "i.released", "productionyear": "i.year",
                 "communityrating": "i.rating"}.get(sort, "i.sort_title")
        desc = (arg(req, "SortOrder", "sortOrder", default="Ascending") or "").lower().startswith("desc")
        total = db.scalar("SELECT COUNT(*) FROM (" + sql + ")", args + age_args)
        sql += f" ORDER BY {order} {'DESC' if desc else 'ASC'}, i.id"
        if limit > 0:
            sql += f" LIMIT {min(limit, 5000)} OFFSET {start}"
        elif start:
            sql += f" LIMIT -1 OFFSET {start}"
        rows = db.all(sql, args + age_args)
        return result([item_dto(r, fields, cache) for r in rows], start, total)

    @app.compat_route("GET", "/Items")
    def jf_items(req):
        return query_items(req, token_user(req))

    @app.compat_route("GET", "/Users/(?P<uid>Me|[0-9a-fA-F-]{32,36})/Items")
    def jf_user_items(req):
        return query_items(req, target_user(req, req.params["uid"])[1])

    @app.compat_route("GET", "/Items/(?P<iid>[0-9a-fA-F-]{32,36})")
    def jf_item(req):
        u = token_user(req)
        cache = {}
        row = item_by_guid(u, req.params["iid"])
        if not row:
            raise HTTPError(404, "Item not found")
        return item_dto(row, fields_of(req) | {"mediasources"}, cache)

    @app.compat_route("GET", "/Users/(?P<uid>Me|[0-9a-fA-F-]{32,36})/Items/(?P<iid>[0-9a-fA-F-]{32,36})")
    def jf_user_item(req):
        u = target_user(req, req.params["uid"])[1]
        cache = {}
        row = item_by_guid(u, req.params["iid"])
        if not row:
            raise HTTPError(404, "Item not found")
        return item_dto(row, fields_of(req) | {"mediasources"}, cache)

    def latest(req, user):
        cache = {}
        limit = max(1, min(100, int(arg(req, "Limit", "limit", default="20") or 20)))
        parent = arg(req, "ParentId", "parentId")
        lib = unguid(parent, K_LIB) if parent else None
        allowed = x.allowed_set(user)
        if lib is not None and str(lib) not in allowed:
            return []
        libs = [lib] if lib is not None else [int(a) for a in allowed]
        if not libs:
            return []
        age_sql, age_args = x.age_filter(user)
        rows = db.all(x.ITEM_SQL + f"WHERE i.library_id IN ({','.join('?' * len(libs))}) "
                      "AND i.type IN ('movie','episode')" + age_sql + " ORDER BY i.added_at DESC, i.id DESC LIMIT ?",
                      libs + age_args + [limit * 4])
        out, seen = [], set()
        for r in rows:
            key = ("s", r["show_id"]) if r["type"] == "episode" else ("m", r["id"])
            if key in seen:
                continue
            seen.add(key)
            out.append(item_dto(r, fields_of(req), cache))
            if len(out) >= limit:
                break
        return out

    @app.compat_route("GET", "/Items/Latest")
    def jf_latest(req):
        u = token_user(req)
        uid = arg(req, "userId", "UserId")
        if uid and uid.lower() != "me":
            u = target_user(req, uid)[1]
        return latest(req, u)

    @app.compat_route("GET", "/Users/(?P<uid>Me|[0-9a-fA-F-]{32,36})/Items/Latest")
    def jf_user_latest(req):
        return latest(req, target_user(req, req.params["uid"])[1])

    def show_of(req, user):
        row = item_by_guid(user, req.params["sid"])
        if not row or row["type"] != "show":
            raise HTTPError(404, "Series not found")
        return row

    @app.compat_route("GET", "/Shows/(?P<sid>[0-9a-fA-F-]{32,36})/Seasons")
    def jf_seasons(req):
        u = token_user(req)
        cache = {}
        show = show_of(req, u)
        rows = db.all(x.ITEM_SQL + "WHERE i.parent_id=? AND i.type='season' ORDER BY i.idx=0, i.idx", (show["id"],))
        return result([item_dto(r, fields_of(req), cache) for r in rows])

    @app.compat_route("GET", "/Shows/(?P<sid>[0-9a-fA-F-]{32,36})/Episodes")
    def jf_episodes(req):
        u = token_user(req)
        cache = {}
        show = show_of(req, u)
        sql, args = x.ITEM_SQL + "WHERE i.show_id=? AND i.type='episode'", [show["id"]]
        season = arg(req, "seasonId", "SeasonId")
        if season:
            n = unguid(season, K_ITEM)
            if n is None:
                return result([])
            sql += " AND i.parent_id=?"
            args.append(n)
        elif arg(req, "season", "Season") not in (None, ""):
            sql += " AND p.idx=?"
            args.append(int(arg(req, "season", "Season")))
        rows = db.all(sql + " " + x.EPISODE_ORDER, args)
        return result([item_dto(r, fields_of(req), cache) for r in rows])

    # ------------------------------------------------------------ côté Séance (administration)
    x.jf_guid = guid

    @app.route("GET", "/api/admin/jellyfin", auth="admin")
    def jf_admin(req):
        rows = db.all("SELECT t.token_hash, t.kind, t.app, t.device, t.ip, t.created_at, t.last_used, u.username "
                      "FROM jf_tokens t JOIN users u ON u.id=t.user_id ORDER BY t.last_used DESC LIMIT 100")
        return {"enabled": enabled(), "server_id": server_id(),
                "tokens": [{"id": r["token_hash"][:16], "kind": r["kind"], "app": r["app"], "device": r["device"],
                            "user": r["username"], "ip": r["ip"], "created_at": r["created_at"],
                            "last_used": r["last_used"]} for r in rows]}

    @app.route("PUT", "/api/admin/jellyfin", auth="admin")
    def jf_admin_set(req):
        b = req.json()
        if "enabled" in b:
            db.set_setting("jellyfin_api", "1" if b["enabled"] else "0")
        return jf_admin(req)

    @app.route("DELETE", "/api/admin/jellyfin/tokens/(?P<tid>[0-9a-f]{16})", auth="admin")
    def jf_admin_revoke(req):
        db.run("DELETE FROM jf_tokens WHERE substr(token_hash,1,16)=?", (req.params["tid"],))
        return {"ok": True}
