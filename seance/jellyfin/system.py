"""Serveur, comptes et préférences vus par les clients Jellyfin."""
import json
import re
import time

import ipaddress

from ..auth import Throttle, check_credentials, hash_password, validate_password, verify_password
from ..catalog import validate_prefs
from ..web import HTTPError, Response
from .common import GUID_RE, JF_VERSION, LANG2, iso, no_content, result, token_hash

U = rf"(?P<uid>Me|{GUID_RE})"
CULTURES = [("fr", "fra", "French", "Français"), ("en", "eng", "English", "English"), ("de", "deu", "German", "Deutsch"),
            ("es", "spa", "Spanish", "Español"), ("it", "ita", "Italian", "Italiano"),
            ("pt", "por", "Portuguese", "Português"), ("ja", "jpn", "Japanese", "日本語"),
            ("ko", "kor", "Korean", "한국어"), ("zh", "zho", "Chinese", "中文"), ("nl", "nld", "Dutch", "Nederlands")]
LANG3 = {v: k for k, v in LANG2.items()}


def local_address(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def register(app, ctx, x, jf):
    db = ctx.db
    route = app.compat_route
    # code PIN (4 à 8 chiffres) : 5 essais par heure et par profil, depuis le réseau local seulement
    pin_throttle = Throttle(limit=5, window=3600)

    # ------------------------------------------------------------ système
    def info(public, req=None):
        host = (req.headers.get("Host") if req else "") or ""
        d = {"LocalAddress": f"http://{host}" if host else "", "ServerName": jf.server_name(), "Version": JF_VERSION,
             "ProductName": "Jellyfin Server", "OperatingSystem": "", "Id": jf.server_id(),
             "StartupWizardCompleted": True}
        if not public:
            d.update({"OperatingSystemDisplayName": "", "PackageName": "seance", "HasPendingRestart": False,
                      "IsShuttingDown": False, "SupportsLibraryMonitor": True, "WebSocketPortNumber": 0,
                      "CompletedInstallations": [], "CanSelfRestart": False, "CanLaunchWebBrowser": False,
                      "ProgramDataPath": "", "WebPath": "", "ItemsByNamePath": "", "CachePath": "", "LogPath": "",
                      "InternalMetadataPath": "", "TranscodingTempPath": "", "CastReceiverApplications": [],
                      "HasUpdateAvailable": False, "EncoderLocation": "System", "SystemArchitecture": "X64"})
        return d

    @route("GET", "/System/Info/Public")
    def jf_info_public(req):
        jf.require_enabled()
        return info(True, req)

    @route("GET", "/System/Info")
    def jf_info(req):
        jf.token_user(req)
        return info(False, req)

    def ping(req):
        jf.require_enabled()
        return Response(json.dumps("Jellyfin Server").encode(), 200, "application/json; charset=utf-8")
    route("GET", "/System/Ping")(ping)
    route("POST", "/System/Ping")(ping)

    @route("GET", "/System/Endpoint")
    def jf_endpoint(req):
        jf.token_user(req)
        return {"IsLocal": False, "IsInNetwork": True}

    @route("GET", "/GetUtcTime")
    def jf_utc(req):
        jf.require_enabled()
        now = iso(time.time())
        return {"RequestReceptionTime": now, "ResponseTransmissionTime": now}

    @route("GET", "/System/Configuration")
    def jf_config(req):
        jf.token_user(req)
        return {"LogFileRetentionDays": 3, "IsStartupWizardCompleted": True, "EnableMetrics": False,
                "EnableNormalizedItemByNameIds": True, "IsPortAuthorized": True, "QuickConnectAvailable": False,
                "EnableCaseSensitiveItemIds": True, "DisableLiveTvChannelUserDataName": True, "MetadataPath": "",
                "PreferredMetadataLanguage": "fr", "MetadataCountryCode": "FR", "SortReplaceCharacters": [],
                "SortRemoveCharacters": [], "SortRemoveWords": [], "MinResumePct": 5, "MaxResumePct": 90,
                "MinResumeDurationSeconds": 30, "MinAudiobookResume": 5, "MaxAudiobookResume": 5,
                "InactiveSessionThreshold": 0, "LibraryMonitorDelay": 60, "LibraryUpdateDuration": 30,
                "CacheSize": 0, "ImageSavingConvention": "Compatible", "MetadataOptions": [],
                "SkipDeserializationForBasicTypes": True, "ServerName": jf.server_name(), "UICulture": "fr",
                "SaveMetadataHidden": False, "ContentTypes": [], "RemoteClientBitrateLimit": 0,
                "EnableFolderView": False, "EnableGroupingMoviesIntoCollections": False,
                "EnableGroupingShowsIntoCollections": False, "DisplaySpecialsWithinSeasons": True, "CodecsUsed": [],
                "PluginRepositories": [], "EnableExternalContentInSuggestions": False,
                "ImageExtractionTimeoutMs": 0, "PathSubstitutions": [], "EnableSlowResponseWarning": False,
                "SlowResponseThresholdMs": 500, "CorsHosts": [], "LibraryScanFanoutConcurrency": 0,
                "LibraryMetadataRefreshConcurrency": 0, "AllowClientLogUpload": False, "DummyChapterDuration": 0,
                "ChapterImageResolution": "MatchSource", "ParallelImageEncodingLimit": 0,
                "CastReceiverApplications": [], "TrickplayOptions": {
                    "EnableHwAcceleration": False, "EnableHwEncoding": False, "EnableKeyFrameOnlyExtraction": True,
                    "ScanBehavior": "NonBlocking", "ProcessPriority": "BelowNormal", "Interval": 10000,
                    "WidthResolutions": [240], "TileWidth": 10, "TileHeight": 10, "Qscale": 4, "JpegQuality": 90,
                    "ProcessThreads": 1}}

    @route("GET", r"/System/Configuration/(?P<key>\w{1,40})")
    def jf_named_config(req):
        jf.token_user(req)
        key = req.params["key"].lower()
        if key == "branding":
            return {"LoginDisclaimer": "", "CustomCss": "", "SplashscreenEnabled": False}
        if key == "encoding":
            return {"EnableThrottling": True, "EnableSegmentDeletion": True, "HardwareAccelerationType": "none",
                    "AllowHevcEncoding": False, "AllowAv1Encoding": False, "EnableTonemapping": False}
        return {}

    @route("GET", "/Branding/Configuration")
    def jf_branding(req):
        jf.require_enabled()
        return {"LoginDisclaimer": "", "CustomCss": "", "SplashscreenEnabled": False}

    def css(req):
        jf.require_enabled()
        return Response(b"", 200, "text/css; charset=utf-8", {"Cache-Control": "no-cache"})
    route("GET", "/Branding/Css")(css)
    route("GET", r"/Branding/Css\.css")(css)

    @route("GET", "/Branding/Splashscreen")
    def jf_splash(req):
        jf.require_enabled()
        raise HTTPError(404, "Not found")

    # ------------------------------------------------------------ localisation
    @route("GET", "/Localization/Cultures")
    def jf_cultures(req):
        jf.token_user(req)
        return [{"Name": n, "DisplayName": d, "TwoLetterISOLanguageName": two, "ThreeLetterISOLanguageName": three,
                 "ThreeLetterISOLanguageNames": [three]} for two, three, n, d in CULTURES]

    @route("GET", "/Localization/Countries")
    def jf_countries(req):
        jf.token_user(req)
        return [{"Name": c, "DisplayName": n, "TwoLetterISORegionName": c, "ThreeLetterISORegionName": t}
                for c, t, n in (("FR", "FRA", "France"), ("BE", "BEL", "Belgique"), ("CH", "CHE", "Suisse"),
                                ("CA", "CAN", "Canada"), ("US", "USA", "United States"), ("GB", "GBR", "United Kingdom"))]

    @route("GET", "/Localization/ParentalRatings")
    def jf_ratings(req):
        jf.token_user(req)
        return [{"Name": n, "Value": v} for n, v in (("FR-U", 0), ("FR-10", 10), ("FR-12", 12), ("FR-16", 16),
                                                      ("FR-18", 18))]

    @route("GET", "/Localization/Options")
    def jf_loc_options(req):
        jf.token_user(req)
        return [{"Name": "Français", "Value": "fr"}, {"Name": "English", "Value": "en-US"}]

    # ------------------------------------------------------------ connexion
    @route("GET", "/QuickConnect/Enabled")
    def jf_qc_enabled(req):
        jf.require_enabled()
        return False

    @route("POST", "/QuickConnect/Initiate")
    def jf_qc_initiate(req):
        jf.require_enabled()
        raise HTTPError(401, "Quick connect is disabled")

    @route("GET", "/Users/Public")
    def jf_public_users(req):
        jf.require_enabled()
        return []  # pas de liste de comptes sur l'écran de connexion

    @route("POST", "/Users/AuthenticateByName")
    def jf_login(req):
        jf.require_enabled()
        b = jf.body(req)
        username = str(b.get("Username") or b.get("username") or "").strip()[:64]
        password = str(b.get("Pw") or b.get("pw") or b.get("Password") or "")[:256]
        keys = ("ip:" + req.client_ip, "u:" + username.lower())
        ctx.throttle.check(*keys)
        user = None
        if username and password:
            row = db.one("SELECT * FROM users WHERE username=?", (username,))
            if row and row.get("managed"):
                # profil géré (sans mot de passe) : connexion avec son code PIN, s'il en a un, et seulement
                # depuis le réseau local (un code de 4 chiffres ne doit pas être exposé à Internet)
                pin_key = f"pin:{row['id']}"
                pin_throttle.check(pin_key)
                if row.get("pin_hash") and not row["disabled"] and local_address(req.client_ip) and \
                        verify_password(password[:8], row["pin_hash"]):
                    user = row
                    pin_throttle.reset(pin_key)
                else:
                    pin_throttle.fail(pin_key)
            else:
                user = check_credentials(db, username, password)
        if not user:
            ctx.throttle.fail(*keys)
            raise HTTPError(401, "Invalid username or password")
        ctx.throttle.reset(keys[1])
        fields, _ = jf.auth_fields(req)
        token = jf.issue(user["id"], fields, req.client_ip)
        user.pop("password_hash", None)
        user.pop("pin_hash", None)
        return {"User": jf.user_dto(user), "AccessToken": token, "ServerId": jf.server_id(),
                "SessionInfo": jf.session_dto(user, fields)}

    @route("POST", "/Sessions/Logout")
    def jf_logout(req):
        jf.require_enabled()
        _, token = jf.auth_fields(req)
        if token:
            h = token_hash(token)
            for s in db.all("SELECT id FROM streams WHERE plex_session=?", ("jf:" + h[:16],)):
                x.stop_stream(s["id"])
            db.run("DELETE FROM jf_tokens WHERE token_hash=?", (h,))
        return no_content()

    # ------------------------------------------------------------ utilisateurs
    @route("GET", "/Users")
    def jf_users(req):
        return [jf.user_dto(jf.token_user(req))]

    @route("GET", f"/Users/{U}")
    def jf_user(req):
        return jf.user_dto(jf.target_user(req, req.params["uid"]))

    @route("GET", "/Users/Me")
    def jf_me(req):
        return jf.user_dto(jf.token_user(req))

    def save_config(req, user):
        b = jf.body(req)
        prefs = {}
        audio = LANG3.get(str(b.get("AudioLanguagePreference") or ""), b.get("AudioLanguagePreference"))
        sub = LANG3.get(str(b.get("SubtitleLanguagePreference") or ""), b.get("SubtitleLanguagePreference"))
        if isinstance(audio, str) and re.fullmatch(r"[a-z]{3}", audio):
            prefs["audio_lang"] = audio
        if isinstance(sub, str) and re.fullmatch(r"[a-z]{3}", sub):
            prefs["sub_lang"] = sub
        mode = {"None": "never", "Always": "always", "Smart": "foreign", "Default": "foreign",
                "OnlyForced": "never"}.get(b.get("SubtitleMode"))
        if mode:
            prefs["sub_mode"] = mode
        if isinstance(b.get("EnableNextEpisodeAutoPlay"), bool):
            prefs["autoplay_next"] = b["EnableNextEpisodeAutoPlay"]
        if prefs:
            current = x.get_prefs(user["id"])
            current.update(validate_prefs(prefs))
            db.run("INSERT INTO prefs(user_id,data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data",
                   (user["id"], json.dumps(current)))
        return no_content()

    route("POST", f"/Users/{U}/Configuration")(lambda req: save_config(req, jf.target_user(req, req.params["uid"])))
    route("POST", "/Users/Configuration")(lambda req: save_config(req, jf.query_user(req)))

    def change_password(req, user):
        if user.get("managed"):
            raise HTTPError(403, "Forbidden")
        b = jf.body(req)
        row = db.one("SELECT password_hash FROM users WHERE id=?", (user["id"],))
        key = f"pw:{user['id']}"
        ctx.throttle.check(key)
        if not verify_password(str(b.get("CurrentPw") or b.get("CurrentPassword") or "")[:256], row["password_hash"]):
            ctx.throttle.fail(key)
            raise HTTPError(403, "Invalid password")
        new = b.get("NewPw")
        try:
            validate_password(new)
        except HTTPError as e:
            raise HTTPError(400, e.message)
        db.run("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new), user["id"]))
        # comme dans Séance : les autres sessions (navigateurs, autres appareils) sont fermées
        db.run("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        db.run("DELETE FROM jf_tokens WHERE user_id=? AND token_hash<>?", (user["id"], user["t_hash"]))
        return no_content()

    route("POST", f"/Users/{U}/Password")(lambda req: change_password(req, jf.target_user(req, req.params["uid"])))
    route("POST", "/Users/Password")(lambda req: change_password(req, jf.query_user(req)))

    def forbidden(req):
        jf.token_user(req)
        raise HTTPError(403, "Forbidden")
    for path in (f"/Users/{U}/Policy", "/Users/New", f"/Users/{U}/Images/\\w+", "/Users/ForgotPassword",
                 "/Library/Refresh", "/System/Restart", "/System/Shutdown"):
        route("POST", path)(forbidden)
    route("DELETE", f"/Users/{U}")(forbidden)

    # ------------------------------------------------------------ sessions et appareils
    @route("GET", "/Sessions")
    def jf_sessions(req):
        user = jf.token_user(req)
        return [jf.session_dto(user)]

    def capabilities(req):
        jf.token_user(req)
        return no_content()
    route("POST", "/Sessions/Capabilities")(capabilities)
    route("POST", "/Sessions/Capabilities/Full")(capabilities)
    route("POST", "/Sessions/Viewing")(capabilities)
    route("POST", r"/Sessions/(?P<sid>[\w-]{1,64})/(?:Playing|Message|Command|System)(?:/\w+)?")(capabilities)

    @route("POST", r"/ClientLog/Document")
    def jf_client_log(req):
        jf.token_user(req)
        return {"FileName": "ignored.log"}

    # ------------------------------------------------------------ préférences d'affichage
    def prefs_key(req, user):
        client = (jf.q(req, "client") or "emby")[:40]
        return user["id"], client, req.params["pid"][:64]

    @route("GET", r"/DisplayPreferences/(?P<pid>[\w.-]{1,64})")
    def jf_display_prefs(req):
        user = jf.query_user(req)
        uid, client, pid = prefs_key(req, user)
        raw = db.scalar("SELECT data FROM jf_prefs WHERE user_id=? AND client=? AND pref_id=?", (uid, client, pid))
        d = {"Id": pid, "SortBy": "SortName", "RememberIndexing": False, "PrimaryImageHeight": 250,
             "PrimaryImageWidth": 250, "CustomPrefs": {}, "ScrollDirection": "Horizontal", "ShowBackdrop": True,
             "RememberSorting": False, "SortOrder": "Ascending", "ShowSidebar": False, "Client": client}
        if raw:
            try:
                saved = json.loads(raw)
                if isinstance(saved, dict):
                    d.update(saved)
            except ValueError:
                pass
        d["Id"], d["Client"] = pid, client
        return d

    @route("POST", r"/DisplayPreferences/(?P<pid>[\w.-]{1,64})")
    def jf_display_prefs_set(req):
        user = jf.query_user(req)
        uid, client, pid = prefs_key(req, user)
        b = jf.body(req)
        keep = {k: b[k] for k in ("SortBy", "SortOrder", "ViewType", "IndexBy", "RememberIndexing", "RememberSorting",
                                  "PrimaryImageHeight", "PrimaryImageWidth", "ScrollDirection", "ShowBackdrop",
                                  "ShowSidebar", "CustomPrefs") if k in b}
        custom = keep.get("CustomPrefs")
        if not isinstance(custom, dict) or len(custom) > 200:
            keep["CustomPrefs"] = {}
        else:
            keep["CustomPrefs"] = {str(k)[:100]: (str(v)[:2000] if v is not None else None)
                                   for k, v in custom.items()}
        data = json.dumps(keep, ensure_ascii=False)
        if len(data) > 64 * 1024:
            raise HTTPError(413, "Too large")
        if db.scalar("SELECT COUNT(*) FROM jf_prefs WHERE user_id=?", (uid,)) > 500:
            db.run("DELETE FROM jf_prefs WHERE user_id=? AND client=?", (uid, client))
        db.run("INSERT INTO jf_prefs(user_id,client,pref_id,data) VALUES(?,?,?,?) ON CONFLICT(user_id,client,pref_id) "
               "DO UPDATE SET data=excluded.data", (uid, client, pid, data))
        return no_content()

    # ------------------------------------------------------------ fonctions absentes de Séance (réponses vides)
    def empty_list(req):
        jf.token_user(req)
        return []

    def empty_result(req):
        jf.token_user(req)
        return result([])

    for path in ("/Plugins", "/Packages", "/ScheduledTasks", "/Repositories", "/SyncPlay/List",
                 "/Notifications/Types", "/Auth/Providers", "/Auth/PasswordResetProviders", "/web/ConfigurationPages",
                 "/Users/(?:Me|[0-9a-fA-F-]{32,36})/Suggestions", "/LiveTv/Channels/Groups"):
        route("GET", path)(empty_list)
    for path in ("/LiveTv/Channels", "/LiveTv/Programs", "/LiveTv/Programs/Recommended", "/LiveTv/Recordings",
                 "/LiveTv/Recordings/Folders", "/LiveTv/Recordings/Series", "/LiveTv/Timers", "/LiveTv/SeriesTimers",
                 "/Devices", "/Auth/Keys", "/System/ActivityLog/Entries", "/Items/Suggestions"):
        route("GET", path)(empty_result)
    route("POST", "/LiveTv/Programs")(empty_result)

    @route("GET", "/LiveTv/Info")
    def jf_livetv_info(req):
        jf.token_user(req)
        return {"Services": [], "IsEnabled": False, "EnabledUsers": []}

    @route("GET", "/LiveTv/GuideInfo")
    def jf_guide_info(req):
        jf.token_user(req)
        now = iso(time.time())
        return {"StartDate": now, "EndDate": now}

    @route("GET", "/Library/MediaFolders")
    def jf_media_folders(req):
        user = jf.token_user(req)
        return result([jf.lib_dto(l, user) for l in x.libraries_all() if l["id"] in x.allowed_set(user)])

    @route("GET", "/Library/VirtualFolders")
    def jf_virtual_folders(req):
        jf.token_user(req)
        raise HTTPError(403, "Forbidden")
