"""Compatibilité avec les applications Jellyfin : parcours d'un client comme le font Jellyfin Web /
Jellyfin Media Player (Windows) et l'application Android TV, réponses vérifiées contre le SDK officiel.

python3 -m unittest tests.test_jellyfin
"""
import json
import os
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from tests.jf_schema import assert_model
from tests import test_api

WEB_DIR = tempfile.mkdtemp()
with open(os.path.join(WEB_DIR, "index.html"), "w") as f:
    f.write("<!doctype html><title>Jellyfin</title>")
os.makedirs(os.path.join(WEB_DIR, "assets"))
with open(os.path.join(WEB_DIR, "assets", "main.js"), "w") as f:
    f.write("console.log(1)")
os.environ["SEANCE_JELLYFIN_WEB"] = WEB_DIR

ANDROID_TV = {  # résumé de jellyfin-androidtv (util/profile/deviceProfile.kt)
    "Name": "AndroidTV-Default", "MaxStreamingBitrate": 120_000_000,
    "DirectPlayProfiles": [{"Type": "Video", "Container": "asf,hls,m4v,mkv,mov,mp4,ogm,ogv,ts,vob,webm,wmv,xvid",
                            "VideoCodec": "av1,h264,hevc,mpeg,mpeg2video,vc1,vp8,vp9",
                            "AudioCodec": "aac,ac3,eac3,mp3,opus,flac,dts,truehd,vorbis"}],
    "TranscodingProfiles": [
        {"Type": "Video", "Context": "Streaming", "Protocol": "hls", "Container": "ts", "VideoCodec": "hevc,h264",
         "AudioCodec": "aac,mp3,ac3,eac3", "CopyTimestamps": False, "EnableSubtitlesInManifest": True},
        {"Type": "Video", "Context": "Streaming", "Protocol": "hls", "Container": "mp4", "VideoCodec": "hevc,h264",
         "AudioCodec": "aac,mp3,ac3,eac3"}],
    "ContainerProfiles": [],
    "CodecProfiles": [
        {"Type": "Video", "Codec": "h264", "Conditions": [
            {"Condition": "EqualsAny", "Property": "VideoProfile", "Value": "high|main|baseline|constrained baseline",
             "IsRequired": False},
            {"Condition": "LessThanEqual", "Property": "VideoLevel", "Value": "52", "IsRequired": False}]},
        {"Type": "Video", "Codec": "hevc", "Conditions": [
            {"Condition": "EqualsAny", "Property": "VideoProfile", "Value": "main|main 10", "IsRequired": False}]}],
    "SubtitleProfiles": [{"Format": f, "Method": m} for f in ("srt", "subrip", "ass", "ssa", "pgs", "pgssub", "vtt")
                         for m in ("Embed", "External") if not (f in ("pgs", "pgssub") and m == "External")],
}
CHROME = {  # résumé de jellyfin-web (scripts/browserDeviceProfile.js) pour Chrome
    "MaxStreamingBitrate": 120_000_000,
    "DirectPlayProfiles": [{"Container": "webm", "Type": "Video", "VideoCodec": "vp8,vp9,av1", "AudioCodec": "vorbis,opus"},
                           {"Container": "mp4,m4v", "Type": "Video", "VideoCodec": "h264,vp9,av1",
                            "AudioCodec": "aac,mp3,opus,flac"}],
    "TranscodingProfiles": [{"Container": "ts", "Type": "Video", "AudioCodec": "aac,mp3", "VideoCodec": "h264",
                             "Context": "Streaming", "Protocol": "hls", "MaxAudioChannels": "2", "MinSegments": "1",
                             "BreakOnNonKeyFrames": True}],
    "ContainerProfiles": [],
    "CodecProfiles": [
        {"Type": "VideoAudio", "Codec": "aac", "Conditions": [
            {"Condition": "Equals", "Property": "IsSecondaryAudio", "Value": "false", "IsRequired": False}]},
        {"Type": "Video", "Codec": "h264", "Conditions": [
            {"Condition": "NotEquals", "Property": "IsAnamorphic", "Value": "true", "IsRequired": False},
            {"Condition": "EqualsAny", "Property": "VideoProfile", "Value": "high|main|baseline|constrained baseline",
             "IsRequired": False},
            {"Condition": "EqualsAny", "Property": "VideoRangeType", "Value": "SDR", "IsRequired": False},
            {"Condition": "LessThanEqual", "Property": "VideoLevel", "Value": "52", "IsRequired": False}]}],
    "SubtitleProfiles": [{"Format": "vtt", "Method": "External"}, {"Format": "ass", "Method": "External"},
                         {"Format": "ssa", "Method": "External"}, {"Format": "pgssub", "Method": "External"}],
}


class JellyfinTest(unittest.TestCase):
    setUpClass = classmethod(test_api.SeanceTest.setUpClass.__func__)
    tearDownClass = classmethod(test_api.SeanceTest.tearDownClass.__func__)

    # ------------------------------------------------------------ outils
    def jf(self, method, path, body=None, token=None, device="dev-1", raw=False, headers=None):
        auth = f'MediaBrowser Client="Jellyfin%20Android%20TV", Device="Salon", DeviceId="{device}", Version="0.19.0"'
        if token:
            auth += f', Token="{token}"'
        h = {"Authorization": auth, "Content-Type": "application/json", "Accept": "application/json"}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else (b"" if method in ("POST", "DELETE") else None)
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=120) as r:
                content, st, hdrs = r.read(), r.status, dict(r.headers)
        except urllib.error.HTTPError as e:
            content, st, hdrs = e.read(), e.code, dict(e.headers)
        if raw:
            return st, content, hdrs
        try:
            return st, json.loads(content) if content else None
        except ValueError:
            return st, content

    def enable(self, on=True):
        st, r = self.admin.call("PUT", "/api/admin/jellyfin", {"enabled": on})
        self.assertEqual(st, 200, r)
        return r

    def login(self, user="colas", pw="motdepasse1", device="dev-1"):
        st, r = self.jf("POST", "/Users/AuthenticateByName", {"Username": user, "Pw": pw}, device=device)
        self.assertEqual(st, 200, r)
        return r

    def setUp(self):
        self.enable(True)

    def views(self, token):
        st, r = self.jf("GET", "/UserViews", token=token)
        self.assertEqual(st, 200, r)
        return {v["Name"]: v for v in r["Items"]}

    def find(self, token, lib, name, types="Movie"):
        v = self.views(token)[lib]
        st, r = self.jf("GET", f"/Items?ParentId={v['Id']}&IncludeItemTypes={types}&Recursive=true&Fields=Overview",
                        token=token)
        self.assertEqual(st, 200, r)
        return next(i for i in r["Items"] if i["Name"] == name)

    # ------------------------------------------------------------ serveur et connexion
    def test_disabled_by_default_and_toggle(self):
        self.enable(False)
        st, body, _ = self.jf("GET", "/System/Info/Public", raw=True)
        self.assertFalse(body.startswith(b"{"), body[:80])  # page de Nexis, pas un serveur Jellyfin
        self.assertEqual(self.jf("POST", "/Users/AuthenticateByName", {"Username": "colas", "Pw": "motdepasse1"})[0], 405)
        self.enable(True)
        st, pub = self.jf("GET", "/System/Info/Public")
        self.assertEqual((st, pub["ProductName"], pub["Version"]), (200, "Jellyfin Server", "10.11.11"))
        assert_model(self, pub, "PublicSystemInfo")
        self.assertEqual(self.jf("GET", "/System/Info")[0], 401)

    def test_login_user_and_policy(self):
        self.assertEqual(self.jf("POST", "/Users/AuthenticateByName", {"Username": "colas", "Pw": "faux"})[0], 401)
        r = self.login()
        assert_model(self, r, "AuthenticationResult")
        self.assertFalse(r["User"]["Policy"]["IsAdministrator"])  # l'administration reste dans Nexis
        tok = r["AccessToken"]
        st, info = self.jf("GET", "/System/Info", token=tok)
        assert_model(self, info, "SystemInfo")
        st, me = self.jf("GET", "/Users/Me", token=tok)
        assert_model(self, me, "UserDto")
        self.assertEqual(me["Id"], r["User"]["Id"])
        # jeton dans les différentes formes acceptées par Jellyfin
        self.assertEqual(self.jf("GET", f"/Users/{me['Id']}", headers={"X-Emby-Token": tok})[0], 200)
        self.assertEqual(self.jf("GET", f"/Users/Me?api_key={tok}")[0], 200)
        self.assertEqual(self.jf("GET", f"/users/me?ApiKey={tok}")[0], 200)  # chemins insensibles à la casse
        # un client ne lit pas les données d'un autre compte
        lea = self.login("lea", "motdepasse2", device="lea-tv")
        self.assertEqual(self.jf("GET", f"/Users/{lea['User']['Id']}", token=tok)[0], 403)
        self.assertEqual(self.jf("GET", f"/Items?userId={lea['User']['Id']}", token=tok)[0], 403)
        st, users = self.jf("GET", "/Users/Public")
        self.assertEqual(users, [])
        # même appareil : la session précédente est remplacée
        again = self.login("lea", "motdepasse2", device="lea-tv")
        self.assertEqual(self.jf("GET", "/Users/Me", token=lea["AccessToken"])[0], 401)
        st, _ = self.jf("POST", "/Sessions/Logout", token=again["AccessToken"])
        self.assertEqual(st, 204)
        self.assertEqual(self.jf("GET", "/Users/Me", token=again["AccessToken"])[0], 401)

    def test_password_change_and_admin_revoke(self):
        st, u = self.admin.call("POST", "/api/admin/users", {"username": "temp", "password": "motdepasse3",
                                                             "display_name": "Temp"})
        self.assertEqual(st, 200, u)
        tok = self.login("temp", "motdepasse3", device="t1")["AccessToken"]
        tok2 = self.login("temp", "motdepasse3", device="t2")["AccessToken"]
        st, d = self.admin.call("GET", "/api/admin/jellyfin")
        dev = next(x for x in d["devices"] if x["user"] == "temp" and x["device"] == "Salon")
        self.assertEqual(self.admin.call("DELETE", f"/api/admin/jellyfin/devices/{dev['id']}")[0], 200)
        alive = [t for t in (tok, tok2) if self.jf("GET", "/Users/Me", token=t)[0] == 200]
        self.assertEqual(len(alive), 1)
        st, r = self.jf("POST", "/Users/Password", {"CurrentPw": "faux", "NewPw": "nouveaumotdepasse"}, token=alive[0])
        self.assertEqual(st, 403)
        st, r = self.jf("POST", "/Users/Password", {"CurrentPw": "motdepasse3", "NewPw": "nouveaumotdepasse"},
                        token=alive[0])
        self.assertEqual(st, 204, r)
        uid = u["id"] if "id" in u else u["user"]["id"]
        st, r = self.admin.call("PUT", f"/api/admin/users/{uid}", {"disabled": True})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.jf("GET", "/Users/Me", token=alive[0])[0], 401)  # compte désactivé : déconnecté

    def test_managed_profile_pin_and_age_limit(self):
        st, kid = self.admin.call("POST", "/api/admin/users", {"username": "jfkid", "display_name": "Kid",
                                                               "managed": True, "pin": "4321", "max_age": 10,
                                                               "allow_unrated": False})
        self.assertEqual(st, 200, kid)
        self.assertEqual(self.jf("POST", "/Users/AuthenticateByName", {"Username": "jfkid", "Pw": "0000"})[0], 401)
        r = self.login("jfkid", "4321", device="kid-tv")
        tok = r["AccessToken"]
        # contrôle parental : les séries (classées 12) ne sont visibles nulle part, les films « U » si
        st, films = self.jf("GET", "/Items?recursive=true&includeItemTypes=Movie,Series,Episode", token=tok)
        self.assertEqual(sorted(i["Name"] for i in films["Items"]), ["Brume", "Le Grand Voyage", "Nuit polaire"])
        admin_tok = self.login(device="adm")["AccessToken"]
        show = self.find(admin_tok, "Animes", "Kaze no Michi", "Series")
        st, eps = self.jf("GET", f"/Shows/{show['Id']}/Episodes", token=admin_tok)
        ep = eps["Items"][0]["Id"]
        self.assertEqual(self.jf("GET", f"/Items/{show['Id']}", token=tok)[0], 404)
        self.assertEqual(self.jf("POST", f"/Items/{ep}/PlaybackInfo", {"DeviceProfile": ANDROID_TV}, token=tok)[0], 404)
        self.assertEqual(self.jf("GET", f"/Items/{show['Id']}/Images/Primary", token=tok, raw=True)[0], 404)
        self.assertEqual(self.jf("GET", "/Shows/NextUp", token=tok)[1]["Items"], [])
        self.assertEqual(self.jf("GET", "/Search/Hints?searchTerm=kaze", token=tok)[1]["SearchHints"], [])
        st, f = self.jf("GET", "/Items/Filters", token=tok)
        self.assertNotIn("12", f["OfficialRatings"])
        self.assertNotIn("Animation", f["Genres"])
        # changer le code PIN déconnecte les applications de ce profil
        uid = kid.get("id") or kid["user"]["id"]
        self.assertEqual(self.admin.call("PUT", f"/api/admin/users/{uid}", {"pin": "5678"})[0], 200)
        self.assertEqual(self.jf("GET", "/Users/Me", token=tok)[0], 401)

    def test_unknown_jellyfin_routes_are_json(self):
        tok = self.login(device="x")["AccessToken"]
        st, body, h = self.jf("GET", "/Artists/Foo/Images/Primary", token=tok, raw=True)
        self.assertEqual(st, 404)
        self.assertTrue(h["Content-Type"].startswith("application/json"))
        st, body, h = self.jf("GET", "/socket?api_key=" + tok, raw=True)
        self.assertEqual(st, 404)
        st, body, h = self.jf("POST", "/Items/Foo/Refresh", token=tok, raw=True)
        self.assertEqual(st, 404)

    # ------------------------------------------------------------ catalogue
    def test_views_items_and_filters(self):
        tok = self.login()["AccessToken"]
        st, views = self.jf("GET", "/UserViews?userId=" + self.login()["User"]["Id"], token=tok)
        self.assertEqual(st, 401)  # le second login du même appareil a remplacé le jeton
        r = self.login()
        tok, uid = r["AccessToken"], r["User"]["Id"]
        st, views = self.jf("GET", f"/Users/{uid}/Views", token=tok)
        assert_model(self, views, "BaseItemDtoQueryResult")
        libs = {v["Name"]: v for v in views["Items"]}
        self.assertEqual({k: v["CollectionType"] for k, v in libs.items()},
                         {"Films": "movies", "Séries": "tvshows", "Animes": "tvshows"})
        # bibliothèque de films comme la demande Jellyfin Web
        q = (f"/Users/{uid}/Items?SortBy=SortName%2CProductionYear&SortOrder=Ascending&IncludeItemTypes=Movie"
             f"&Recursive=true&Fields=PrimaryImageAspectRatio%2CMediaSourceCount&ImageTypeLimit=1"
             f"&EnableImageTypes=Primary%2CBackdrop%2CBanner%2CThumb&StartIndex=0&ParentId={libs['Films']['Id']}&Limit=100")
        st, films = self.jf("GET", q, token=tok)
        assert_model(self, films, "BaseItemDtoQueryResult")
        self.assertEqual([i["Name"] for i in films["Items"]], ["Brume", "Le Grand Voyage", "Nuit polaire"])
        self.assertEqual(films["TotalRecordCount"], 3)
        gv = films["Items"][1]
        self.assertEqual(gv["ProviderIds"]["Tmdb"], "5001")
        self.assertIn("Primary", gv["ImageTags"])
        # pagination, tri, recherche, genres (paramètres répétés façon SDK Kotlin)
        st, page = self.jf("GET", f"/Items?userId={uid}&parentId={libs['Films']['Id']}&includeItemTypes=Movie"
                                  "&recursive=true&sortBy=SortName&sortOrder=Descending&startIndex=1&limit=1"
                                  "&fields=Overview&fields=Genres", token=tok)
        self.assertEqual(([i["Name"] for i in page["Items"]], page["TotalRecordCount"], page["StartIndex"]),
                         (["Le Grand Voyage"], 3, 1))
        st, found = self.jf("GET", "/Items?searchTerm=voyage&recursive=true&includeItemTypes=Movie,Series", token=tok)
        self.assertEqual([i["Name"] for i in found["Items"]], ["Le Grand Voyage"])
        st, genres = self.jf("GET", f"/Genres?parentId={libs['Films']['Id']}", token=tok)
        assert_model(self, genres, "BaseItemDtoQueryResult")
        aventure = next(g for g in genres["Items"] if g["Name"] == "Aventure")
        st, by_genre = self.jf("GET", f"/Items?recursive=true&includeItemTypes=Movie&genreIds={aventure['Id']}", token=tok)
        self.assertEqual([i["Name"] for i in by_genre["Items"]], ["Le Grand Voyage"])
        st, g = self.jf("GET", f"/Items/{aventure['Id']}", token=tok)
        self.assertEqual((g["Type"], g["Name"]), ("Genre", "Aventure"))
        # fiche complète d'un film
        st, item = self.jf("GET", f"/Items/{gv['Id']}?userId={uid}", token=tok)
        assert_model(self, item, "BaseItemDto")
        self.assertEqual(item["People"][0]["Name"], "Actrice Une")
        self.assertEqual(item["MediaSources"][0]["Container"], "mp4")
        self.assertEqual(item["LocalTrailerCount"], 1)
        st, person = self.jf("GET", f"/Items/{item['People'][0]['Id']}", token=tok)
        self.assertEqual(person["Type"], "Person")
        st, with_person = self.jf("GET", f"/Items?recursive=true&personIds={item['People'][0]['Id']}", token=tok)
        self.assertIn("Le Grand Voyage", [i["Name"] for i in with_person["Items"]])
        st, trailers = self.jf("GET", f"/Items/{gv['Id']}/LocalTrailers", token=tok)
        self.assertEqual(len(trailers), 1)
        assert_model(self, trailers[0], "BaseItemDto")
        st, special = self.jf("GET", f"/Users/{uid}/Items/{gv['Id']}/SpecialFeatures", token=tok)
        self.assertEqual([s["ExtraType"] for s in special], ["Featurette"])
        st, latest = self.jf("GET", f"/Items/Latest?userId={uid}&parentId={libs['Films']['Id']}&limit=16", token=tok)
        self.assertEqual(len(latest), 3)
        for it in latest:
            assert_model(self, it, "BaseItemDto")
        st, counts = self.jf("GET", "/Items/Counts", token=tok)
        assert_model(self, counts, "ItemCounts")
        st, hints = self.jf("GET", "/Search/Hints?searchTerm=brume", token=tok)
        assert_model(self, hints, "SearchHintResult")
        self.assertEqual(hints["SearchHints"][0]["Name"], "Brume")

    def test_series_seasons_episodes_and_library_rights(self):
        r = self.login()
        tok = r["AccessToken"]
        show = self.find(tok, "Séries", "Les Veilleurs", "Series")
        assert_model(self, show, "BaseItemDto")
        self.assertEqual((show["Type"], show["ChildCount"], show["UserData"]["UnplayedItemCount"]), ("Series", 2, 4))
        st, seasons = self.jf("GET", f"/Shows/{show['Id']}/Seasons?userId={r['User']['Id']}", token=tok)
        assert_model(self, seasons, "BaseItemDtoQueryResult")
        self.assertEqual([s["IndexNumber"] for s in seasons["Items"]], [1, 2])
        st, eps = self.jf("GET", f"/Shows/{show['Id']}/Episodes?seasonId={seasons['Items'][0]['Id']}"
                                 "&fields=Overview&fields=MediaSources", token=tok)
        assert_model(self, eps, "BaseItemDtoQueryResult")
        self.assertEqual([(e["ParentIndexNumber"], e["IndexNumber"]) for e in eps["Items"]], [(1, 1), (1, 2), (1, 3)])
        self.assertEqual(eps["Items"][0]["SeriesName"], "Les Veilleurs")
        # épisode voisin (bouton « suivant » des clients)
        st, adj = self.jf("GET", f"/Shows/{show['Id']}/Episodes?adjacentTo={eps['Items'][1]['Id']}", token=tok)
        self.assertEqual([e["IndexNumber"] for e in adj["Items"]], [1, 2, 3])
        # même requête via /Items?ParentId=saison
        st, eps2 = self.jf("GET", f"/Items?ParentId={seasons['Items'][0]['Id']}", token=tok)
        self.assertEqual([e["IndexNumber"] for e in eps2["Items"]], [1, 2, 3])
        st, seasons2 = self.jf("GET", f"/Items?ParentId={show['Id']}", token=tok)
        self.assertEqual([s["IndexNumber"] for s in seasons2["Items"]], [1, 2])
        # vu un épisode -> « à suivre » propose le suivant
        st, ud = self.jf("POST", f"/UserPlayedItems/{eps['Items'][0]['Id']}", token=tok)
        assert_model(self, ud, "UserItemDataDto")
        self.assertTrue(ud["Played"])
        st, nxt = self.jf("GET", "/Shows/NextUp?limit=10&fields=PrimaryImageAspectRatio", token=tok)
        assert_model(self, nxt, "BaseItemDtoQueryResult")
        self.assertIn((show["Id"], 2), [(e["SeriesId"], e["IndexNumber"]) for e in nxt["Items"]])
        st, ud = self.jf("DELETE", f"/UserPlayedItems/{eps['Items'][0]['Id']}", token=tok)
        self.assertFalse(ud["Played"])
        # Léa n'a pas accès à la bibliothèque Séries
        lea = self.login("lea", "motdepasse2", device="lea-phone")["AccessToken"]
        self.assertEqual(set(self.views(lea)), {"Films", "Animes"})
        self.assertEqual(self.jf("GET", f"/Items/{show['Id']}", token=lea)[0], 404)
        self.assertEqual(self.jf("GET", f"/Shows/{show['Id']}/Episodes", token=lea)[0], 404)
        st, all_ = self.jf("GET", "/Items?recursive=true&includeItemTypes=Series", token=lea)
        self.assertEqual([i["Name"] for i in all_["Items"]], ["Kaze no Michi"])
        self.assertEqual(self.jf("POST", f"/Items/{eps['Items'][0]['Id']}/PlaybackInfo", {"DeviceProfile": CHROME},
                                 token=lea)[0], 404)

    def test_images_tags_and_display_prefs(self):
        tok = self.login()["AccessToken"]
        gv = self.find(tok, "Films", "Le Grand Voyage")
        tag = gv["ImageTags"]["Primary"]
        # les images s'affichent sans jeton (<img> de Jellyfin Web) grâce à l'étiquette signée
        st, img, h = self.jf("GET", f"/Items/{gv['Id']}/Images/Primary?fillHeight=300&fillWidth=200&quality=96&tag={tag}",
                             raw=True)
        self.assertEqual((st, h["Content-Type"], img[:2]), (200, "image/jpeg", b"\xff\xd8"))
        self.assertEqual(self.jf("GET", f"/Items/{gv['Id']}/Images/Primary?tag=0000", raw=True)[0], 401)
        self.assertEqual(self.jf("GET", f"/Items/{gv['Id']}/Images/Backdrop/0?maxWidth=1280&tag="
                                        f"{gv['BackdropImageTags'][0]}", raw=True)[0], 200)
        self.assertEqual(self.jf("GET", f"/Items/{gv['Id']}/Images/Primary?maxWidth=200", token=tok, raw=True)[0], 200)
        # préférences d'affichage (enregistrées par appareil)
        st, p = self.jf("GET", "/DisplayPreferences/usersettings?client=emby", token=tok)
        assert_model(self, p, "DisplayPreferencesDto")
        p["CustomPrefs"]["homesection0"] = "resume"
        self.assertEqual(self.jf("POST", "/DisplayPreferences/usersettings?client=emby", p, token=tok)[0], 204)
        st, p2 = self.jf("GET", "/DisplayPreferences/usersettings?client=emby", token=tok)
        self.assertEqual(p2["CustomPrefs"]["homesection0"], "resume")

    # ------------------------------------------------------------ lecture
    def playback_info(self, tok, item_id, profile, **extra):
        body = {"DeviceProfile": profile, "EnableDirectPlay": True, "EnableDirectStream": True,
                "EnableTranscoding": True, "AllowVideoStreamCopy": True, "AllowAudioStreamCopy": True,
                "AutoOpenLiveStream": False, **extra}
        st, r = self.jf("POST", f"/Items/{item_id}/PlaybackInfo", body, token=tok)
        self.assertEqual(st, 200, r)
        assert_model(self, r, "PlaybackInfoResponse")
        self.assertNotIn("ErrorCode", r)
        return r["MediaSources"][0], r["PlaySessionId"]

    def stream_mode(self, sid):
        return json.loads(self.ctx.db.one("SELECT params FROM streams WHERE id=?", (sid,))["params"])

    def test_direct_play_android_tv(self):
        tok = self.login(device="tv")["AccessToken"]
        gv = self.find(tok, "Films", "Le Grand Voyage")
        ms, sid = self.playback_info(tok, gv["Id"], ANDROID_TV)
        self.assertTrue(ms["SupportsDirectPlay"])
        # ExoPlayer demande le fichier sans jeton : l'étiquette (ETag) suffit
        url = f"/Videos/{gv['Id']}/stream?container=mp4&mediaSourceId={ms['Id']}&static=true&tag={ms['ETag']}"
        st, data, h = self.jf("GET", url, raw=True, headers={"Range": "bytes=0-99"})
        self.assertEqual((st, len(data), h["Content-Type"]), (206, 100, "video/mp4"))
        self.assertEqual(self.jf("GET", url.replace(ms["ETag"], "0" * 36), raw=True)[0], 401)
        other = self.find(tok, "Films", "Brume")
        self.assertEqual(self.jf("GET", f"/Videos/{other['Id']}/stream?static=true&tag={ms['ETag']}", raw=True)[0], 401)
        # Brume (HEVC + PGS) : Android TV lit tout directement, sous-titres compris
        ms, _ = self.playback_info(tok, other["Id"], ANDROID_TV, SubtitleStreamIndex=2)
        self.assertTrue(ms["SupportsDirectPlay"])
        pgs = next(s for s in ms["MediaStreams"] if s["Type"] == "Subtitle")
        self.assertEqual((pgs["Codec"], pgs["DeliveryMethod"]), ("PGSSUB", "Embed"))
        # progression : début, avancée, arrêt -> reprise proposée
        dur = gv["RunTimeTicks"]
        for path, pos in (("/Sessions/Playing", 0), ("/Sessions/Playing/Progress", dur // 3),
                          ("/Sessions/Playing/Stopped", dur // 2)):
            st, _ = self.jf("POST", path, {"ItemId": gv["Id"], "PositionTicks": pos, "PlaySessionId": sid,
                                           "MediaSourceId": ms["Id"], "IsPaused": False}, token=tok)
            self.assertEqual(st, 204)
        st, resume = self.jf("GET", "/UserItems/Resume?limit=12&mediaTypes=Video", token=tok)
        assert_model(self, resume, "BaseItemDtoQueryResult")
        item = next(i for i in resume["Items"] if i["Id"] == gv["Id"])
        self.assertAlmostEqual(item["UserData"]["PlaybackPositionTicks"], dur // 2, delta=10_000_000)
        # la position est la même dans Nexis
        st, d = self.admin.call("GET", f"/api/items/{int(gv['Id'][8:], 16)}")
        self.assertAlmostEqual(d["position"], dur // 2 // 10_000, delta=1000)
        # fin de lecture -> vu
        self.jf("POST", "/Sessions/Playing/Stopped", {"ItemId": gv["Id"], "PositionTicks": dur, "PlaySessionId": sid},
                token=tok)
        st, it = self.jf("GET", f"/Items/{gv['Id']}", token=tok)
        self.assertTrue(it["UserData"]["Played"])
        self.jf("DELETE", f"/UserPlayedItems/{gv['Id']}", token=tok)

    def test_web_remux_transcode_and_subtitles(self):
        tok = self.login(device="chrome")["AccessToken"]
        nuit = self.find(tok, "Films", "Nuit polaire")
        # MKV : Chrome ne le lit pas directement ici -> remux HLS (vidéo recopiée, AAC recopié)
        ms, sid = self.playback_info(tok, nuit["Id"], CHROME, AudioStreamIndex=1, SubtitleStreamIndex=-1)
        self.assertFalse(ms["SupportsDirectPlay"])
        self.assertFalse(ms["SupportsDirectStream"])  # sinon Jellyfin Web demanderait le fichier statique
        self.assertTrue(ms["TranscodingUrl"].startswith(f"/videos/{nuit['Id']}/hls/"))
        p = self.stream_mode(sid)
        self.assertEqual((p["mode"], p["acopy"]), ("copy", True))
        # la playlist et les segments se lisent sans jeton (lecteur HLS d'Android TV, hls.js)
        st, pl, _ = self.jf("GET", ms["TranscodingUrl"], raw=True)
        self.assertEqual(st, 200)
        self.assertIn(b"#EXT-X-MAP:URI=\"init.mp4\"", pl)
        base = ms["TranscodingUrl"].split("?")[0].rsplit("/", 1)[0]
        st, init, _ = self.jf("GET", base + "/init.mp4", raw=True)
        st2, seg, _ = self.jf("GET", base + "/0.m4s", raw=True)
        self.assertEqual((st, st2), (200, 200))
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", "-i", "-"],
                               input=init + seg, capture_output=True, timeout=30).stdout.decode().split()
        self.assertEqual(probe[:2], ["h264", "aac"])
        self.assertEqual(self.jf("GET", base.replace(sid, "x" * 22) + "/main.m3u8", raw=True)[0], 404)
        # piste AC3 (japonais) : Chrome ne la lit pas -> audio converti, vidéo toujours recopiée
        ms2, sid2 = self.playback_info(tok, nuit["Id"], CHROME, AudioStreamIndex=2)
        p = self.stream_mode(sid2)
        self.assertEqual((p["mode"], p["acopy"]), ("copy", False))
        # sous-titres : SRT interne et externe livrés en WebVTT / JSON, adresse signée (sans jeton)
        subs = [s for s in ms["MediaStreams"] if s["Type"] == "Subtitle"]
        self.assertEqual([s["DeliveryMethod"] for s in subs], ["External", "External"])
        st, vtt, h = self.jf("GET", subs[0]["DeliveryUrl"], raw=True)
        self.assertEqual(st, 200, vtt)
        self.assertTrue(vtt.startswith(b"WEBVTT"))
        self.assertIn(b"Bonjour tout le monde", vtt)
        st, js = self.jf("GET", subs[0]["DeliveryUrl"].replace(".vtt", ".js"))
        self.assertAlmostEqual(js["TrackEvents"][0]["StartPositionTicks"], 10_000_000, delta=500_000)
        st, ext, _ = self.jf("GET", subs[1]["DeliveryUrl"].replace(".vtt", ".srt"), raw=True)
        self.assertIn(b"Hello everyone", ext)
        self.assertEqual(self.jf("GET", subs[0]["DeliveryUrl"].split("?")[0], raw=True)[0], 401)
        # HEVC : Chrome (profil sans HEVC) -> transcodage H.264
        brume = self.find(tok, "Films", "Brume")
        ms3, sid3 = self.playback_info(tok, brume["Id"], CHROME, SubtitleStreamIndex=-1)
        self.assertEqual(self.stream_mode(sid3)["mode"], "transcode")
        base = ms3["TranscodingUrl"].split("?")[0].rsplit("/", 1)[0]
        st, init, _ = self.jf("GET", base + "/init.mp4", raw=True)
        st, seg, _ = self.jf("GET", base + "/0.m4s", raw=True)
        codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                                "-of", "csv=p=0", "-i", "-"], input=init + seg, capture_output=True,
                               timeout=30).stdout.decode().strip()
        self.assertEqual(codec, "h264")
        # arrêt : le flux est libéré
        self.assertEqual(self.jf("DELETE", f"/Videos/ActiveEncodings?deviceId=chrome&playSessionId={sid3}",
                                 token=tok)[0], 204)
        self.assertIsNone(self.ctx.transcoder.get(sid3))
        # PGS sélectionné sans prise en charge par l'appareil : incrusté dans la vidéo
        no_pgs = dict(CHROME, SubtitleProfiles=[{"Format": "vtt", "Method": "External"}])
        ms4, sid4 = self.playback_info(tok, brume["Id"], no_pgs, SubtitleStreamIndex=2)
        p = self.stream_mode(sid4)
        self.assertEqual((p["mode"], p["burn_index"]), ("transcode", 2))
        for s in (sid, sid2, sid4):
            self.ctx.transcoder.stop(s)

    def test_segments_trickplay_favorites(self):
        tok = self.login(device="tv2")["AccessToken"]
        show = self.find(tok, "Animes", "Kaze no Michi", "Series")
        st, eps = self.jf("GET", f"/Shows/{show['Id']}/Episodes?fields=Chapters&fields=Trickplay", token=tok)
        e1 = eps["Items"][0]
        st, seg = self.jf("GET", f"/MediaSegments/{e1['Id']}?includeSegmentTypes=Intro&includeSegmentTypes=Outro",
                          token=tok)
        self.assertEqual(st, 200, seg)
        assert_model(self, seg, "MediaSegmentDtoQueryResult")
        intro = next(s for s in seg["Items"] if s["Type"] == "Intro")
        self.assertEqual((intro["StartTicks"], intro["EndTicks"]), (100_000_000, 400_000_000))
        self.assertGreaterEqual(len(e1["Chapters"]), 2)
        tag = e1["Chapters"][1]["ImageTag"]
        st, img, _ = self.jf("GET", f"/Items/{e1['Id']}/Images/Chapter/1?tag={tag}&maxWidth=400", raw=True)
        self.assertEqual((st, img[:2]), (200, b"\xff\xd8"))
        # favoris = « Ma liste » de Nexis
        st, ud = self.jf("POST", f"/UserFavoriteItems/{show['Id']}", token=tok)
        self.assertTrue(ud["IsFavorite"])
        st, wl = self.admin.call("GET", "/api/watchlist")
        self.assertIn("Kaze no Michi", [w["title"] for w in wl])
        st, favs = self.jf("GET", "/Items?recursive=true&filters=IsFavorite&includeItemTypes=Series", token=tok)
        self.assertEqual([f["Name"] for f in favs["Items"]], ["Kaze no Michi"])
        self.jf("DELETE", f"/UserFavoriteItems/{show['Id']}", token=tok)
        st, favs = self.jf("GET", "/Items?recursive=true&filters=IsFavorite&includeItemTypes=Series", token=tok)
        self.assertEqual(favs["Items"], [])
        # aperçus (trickplay) : préparés en tâche de fond
        deadline = time.time() + 60
        while time.time() < deadline:
            st, it = self.jf("GET", f"/Items/{e1['Id']}", token=tok)
            if it.get("Trickplay"):
                break
            time.sleep(0.5)
        info = list(list(it["Trickplay"].values())[0].values())[0]
        assert_model(self, info, "TrickplayInfoDto")
        width = list(list(it["Trickplay"].values())[0])[0]
        st, img, _ = self.jf("GET", f"/Videos/{e1['Id']}/Trickplay/{width}/0.jpg?mediaSourceId={e1['Id']}",
                             token=tok, raw=True)
        self.assertEqual((st, img[:2]), (200, b"\xff\xd8"))
        self.assertEqual(self.jf("GET", f"/Videos/{e1['Id']}/Trickplay/{width}/0.jpg", raw=True)[0], 401)

    def test_web_client_files_and_misc(self):
        st, html, h = self.jf("GET", "/web/", raw=True)
        self.assertEqual((st, html), (200, b"<!doctype html><title>Jellyfin</title>"))
        self.assertIn("unsafe-inline", h["Content-Security-Policy"])
        self.assertEqual(self.jf("GET", "/web/assets/main.js", raw=True)[0], 200)
        self.assertEqual(self.jf("GET", "/web/../seance.db", raw=True)[0], 404)
        self.assertEqual(self.jf("GET", "/web/%2e%2e/%2e%2e/etc/passwd", raw=True)[0], 404)
        tok = self.login()["AccessToken"]
        st, b = self.jf("GET", "/Branding/Configuration")
        self.assertEqual(st, 200)
        for path in ("/LiveTv/Info", "/Localization/Cultures", "/web/ConfigurationPages?enableInMainMenu=true",
                     "/Sessions", "/System/Endpoint", "/QuickConnect/Enabled", "/Plugins", "/Studios",
                     "/LiveTv/Programs/Recommended?userId=me", "/Playback/BitrateTest?size=1000"):
            st, r, _ = self.jf("GET", path, token=tok, raw=True)
            self.assertEqual(st, 200, path)
        st, ses = self.jf("GET", "/Sessions", token=tok)
        assert_model(self, ses[0], "SessionInfoDto")
        self.assertEqual(self.jf("POST", "/Sessions/Capabilities/Full", {"PlayableMediaTypes": ["Video"]},
                                 token=tok)[0], 204)
        # l'interface de Nexis reste servie à la racine
        st, page, _ = self.jf("GET", "/", raw=True)
        self.assertIn(b"<html", page.lower()[:500])


class JellyfinUnitTests(unittest.TestCase):
    def test_profile_conditions(self):
        from seance.jellyfin.profile import can_direct_play, contains, subtitle_method
        self.assertTrue(contains("mp4,mkv", "MKV"))
        self.assertFalse(contains("-mp4,ts", "mp4"))
        self.assertTrue(contains("", "avi"))
        probe = {"container": "matroska,webm", "bitrate": 5_000_000,
                 "video": {"codec": "hevc", "profile": "Main 10", "pix_fmt": "yuv420p10le", "width": 3840,
                           "height": 2160, "transfer": "smpte2084"},
                 "audio": [{"index": 1, "codec": "eac3", "channels": 6}]}
        ok, _ = can_direct_play(ANDROID_TV, probe, "/x/film.mkv", probe["audio"][0], 0)
        self.assertTrue(ok)
        ok, why = can_direct_play(CHROME, probe, "/x/film.mkv", probe["audio"][0], 0)
        self.assertFalse(ok)
        hdr_only_sdr = dict(ANDROID_TV, CodecProfiles=[{"Type": "Video", "Codec": "hevc", "Conditions": [
            {"Condition": "EqualsAny", "Property": "VideoRangeType", "Value": "SDR|HLG", "IsRequired": False}]}])
        self.assertFalse(can_direct_play(hdr_only_sdr, probe, "/x/film.mkv", probe["audio"][0], 0)[0])
        self.assertFalse(can_direct_play(ANDROID_TV, probe, "/x/film.mkv", probe["audio"][0], 1_000_000)[0])
        self.assertEqual(subtitle_method(CHROME, "srt", "hls"), ("External", "vtt"))
        self.assertEqual(subtitle_method(ANDROID_TV, "pgssub", "direct", "mkv"), ("Embed", "pgssub"))
        self.assertEqual(subtitle_method({"SubtitleProfiles": [{"Format": "vtt", "Method": "External"}]}, "pgssub",
                                         "hls"), ("Encode", "pgssub"))

    def test_discovery_replies_only_on_lan(self):
        from seance.jellyfin.discovery import Discovery

        class FakeDB:
            def release(self):
                pass

        class FakeJF:
            db = FakeDB()

            def enabled(self):
                return True

            def server_id(self):
                return "a" * 32

            def server_name(self):
                return "Nexis"
        d = Discovery(FakeJF(), 8420)
        reply = d.handle(b"who is JellyfinServer?", ("127.0.0.1", 5000))
        self.assertEqual(json.loads(reply)["Address"], "http://127.0.0.1:8420")
        self.assertIsNone(d.handle(b"who is JellyfinServer?", ("127.0.0.1", 5000)))  # 1 réponse / s
        self.assertIsNone(d.handle(b"who is JellyfinServer?", ("8.8.8.8", 5000)))
        self.assertIsNone(d.handle(b"hello", ("10.0.0.2", 5000)))

    def test_guids(self):
        from seance.jellyfin.common import K_ITEM, K_USER, guid, unguid
        g = guid(K_ITEM, 42)
        self.assertEqual(len(g), 32)
        self.assertEqual(unguid(g, K_ITEM), 42)
        self.assertEqual(unguid(f"{g[:8]}-{g[8:12]}-{g[12:16]}-{g[16:20]}-{g[20:]}", K_ITEM), 42)
        self.assertIsNone(unguid(g, K_USER))
        self.assertIsNone(unguid("../../etc", K_ITEM))


if __name__ == "__main__":
    unittest.main()
