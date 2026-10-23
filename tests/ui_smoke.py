"""Parcours navigateur (Playwright) sur une vraie médiathèque de test + captures d'écran.

python3 tests/ui_smoke.py <dossier_captures> [video_test.webm]

Le Chromium de Playwright ne décode pas le H.264 et hls.js n'est pas embarqué dans le dépôt :
un faux « Hls » lit une vidéo WebM locale après avoir vérifié que la playlist HLS de Séance répond.
"""
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.fixtures import FakeSeerr, FakeTMDB  # noqa: E402
from tests.ui_smoke_stub import serve_webm  # noqa: E402

TMDB = FakeTMDB()
os.environ["SEANCE_TMDB_API"] = TMDB.url
os.environ["SEANCE_TMDB_IMG"] = TMDB.url + "/img"

# faux outils SMB + service de montage (voir tests/test_mounts.py)
import stat  # noqa: E402
from tests.test_mounts import FAKE_MOUNT, FAKE_SMBCLIENT, FAKE_UMOUNT  # noqa: E402
SMB = tempfile.mkdtemp()
os.makedirs(os.path.join(SMB, "bin"))
for _n, _b in (("smbclient", FAKE_SMBCLIENT), ("mount", FAKE_MOUNT), ("umount", FAKE_UMOUNT), ("mount.cifs", "#!/bin/sh\n")):
    with open(os.path.join(SMB, "bin", _n), "w") as _f:
        _f.write(_b)
    os.chmod(os.path.join(SMB, "bin", _n), 0o755)
os.environ["PATH"] = os.path.join(SMB, "bin") + os.pathsep + os.environ["PATH"]
for _k, _v in (("SEANCE_FSTAB", "fstab"), ("SEANCE_MOUNTINFO", "mountinfo"), ("SEANCE_SMB_CRED_DIR", "smb"),
               ("SEANCE_MOUNT_ROOT", "mnt"), ("SEANCE_MOUNTD_SOCK", "run/mountd.sock")):
    os.environ[_k] = os.path.join(SMB, _v)
open(os.environ["SEANCE_FSTAB"], "w").close()
open(os.environ["SEANCE_MOUNTINFO"], "w").close()
from seance import mountd  # noqa: E402
_mounts = mountd.Mounts()
_mounts.env["unprivileged"] = False
_msrv = mountd.make_server(_mounts, os.environ["SEANCE_MOUNTD_SOCK"], allowed_uids={os.getuid()})
threading.Thread(target=_msrv.serve_forever, daemon=True).start()

from playwright.sync_api import sync_playwright  # noqa: E402

from seance.api import Ctx, build_app  # noqa: E402
from seance.web import Server, make_handler  # noqa: E402
from tests.test_api import STATIC, Client, media_root, wait_scan  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "captures"
webm = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.abspath(out)), "test.webm")
os.makedirs(out, exist_ok=True)
work = tempfile.mkdtemp()
media = os.path.join(work, "media")
shutil.copytree(media_root(), media)
ctx = Ctx(os.path.join(work, "data"), STATIC)
code = ctx.setup_code
server = Server(("127.0.0.1", 0), make_handler(build_app(ctx)))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"
errors, failures = [], []

STUB = """
window.__hls = [];
window.Hls = class {
  static isSupported() { return true; }
  static Events = { ERROR: 'error', MANIFEST_PARSED: 'parsed', FRAG_BUFFERED: 'frag' };
  static ErrorTypes = { MEDIA_ERROR: 'media' };
  constructor() { this.h = {}; }
  on(ev, fn) { this.h[ev] = fn; }
  loadSource(u) { fetch(u).then(r => r.text()).then(t => window.__hls.push(t.includes('#EXTINF'))); }
  attachMedia(v) { v.src = '/__test.webm'; v.addEventListener('loadedmetadata', () => this.h.parsed && this.h.parsed(), { once: true }); }
  destroy() {} recoverMediaError() {}
};"""


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("ÉCHEC :", msg)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    bctx = browser.new_context(viewport={"width": 1440, "height": 900}, bypass_csp=True)
    bctx.route("**/vendor/hls.min.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=STUB))
    bctx.route("**/__test.webm", serve_webm(webm))
    page = bctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" and "404" not in m.text and "400" not in m.text else None)

    # --- installation
    page.goto(base + "/")
    page.wait_for_selector("text=Première installation")
    page.fill("input[placeholder='Ex. 4F2A9C1B']", code)
    page.fill("input[autocomplete='username']", "colas")
    page.fill("input[placeholder='Ton prénom']", "Colas")
    page.locator("input[autocomplete='new-password']").nth(0).fill("motdepasse1")
    page.locator("input[autocomplete='new-password']").nth(1).fill("motdepasse1")
    page.screenshot(path=f"{out}/01-installation.png")
    page.click("text=Terminer l’installation")
    page.wait_for_selector("text=Aucune bibliothèque.")

    # --- clé TMDB
    page.click("a.tab:has-text('Métadonnées')")
    page.wait_for_selector("text=Aucune clé TMDB")
    page.fill("input[placeholder^='Clé API']", TMDB.key)
    page.click("section.panel:has-text('TMDB') button.primary")
    page.wait_for_selector("text=Clé TMDB configurée")
    page.screenshot(path=f"{out}/02-reglages-serveur.png", full_page=True)
    check(page.locator("input[value*='/api/hooks/scan?token=']").count() == 1, "adresse du webhook affichée")

    # --- bibliothèque via l'interface, avec le sélecteur de dossier
    page.click("a.tab:has-text('Bibliothèques')")
    page.click("text=+ Ajouter")
    m = page.locator(".modal")
    m.locator("input").first.fill("Films")
    m.locator(".path-row input").fill(media)
    m.locator("text=Parcourir…").click()
    page.wait_for_selector(".folder-list")
    page.locator(".folder", has_text="films").click()
    page.wait_for_function("document.querySelector('.folder-path').textContent.endsWith('/films')")
    page.screenshot(path=f"{out}/03-choix-dossier.png")
    page.locator(".modal", has_text="Choisir un dossier").locator("button.primary").click()
    page.screenshot(path=f"{out}/04-nouvelle-bibliotheque.png")
    page.locator(".modal button.primary").click()
    page.wait_for_selector(".user-row:has-text('Films')")
    admin = Client(base)
    admin.call("POST", "/api/login", {"username": "colas", "password": "motdepasse1"})
    wait_scan(admin)
    for name, sub in (("Séries", "series"), ("Animes", "animes")):
        st, r = admin.call("POST", "/api/admin/libraries", {"name": name, "kind": "show", "paths": [os.path.join(media, sub)]})
        check(st == 200, f"création {name} : {r}")
        wait_scan(admin)
    page.reload()
    page.wait_for_selector(".user-row:has-text('Animes')")
    page.wait_for_timeout(500)
    page.screenshot(path=f"{out}/05-bibliotheques.png", full_page=True)
    check(page.locator(".nav-link").all_inner_texts() == ["Accueil", "À regarder", "Découvrir", "Films", "Séries", "Animes"], "onglets des bibliothèques")

    # --- navigation
    page.goto(base + "/#/")
    page.wait_for_selector(".hero-title")
    page.wait_for_load_state("networkidle")
    page.screenshot(path=f"{out}/06-accueil.png")
    page.click(".nav-link:has-text('Films')")
    page.wait_for_selector(".grid .card")
    page.wait_for_load_state("networkidle")
    check(page.locator(".grid .card").count() == 3, "3 films")
    page.screenshot(path=f"{out}/07-films.png")
    page.click(".nav-link:has-text('Animes')")
    page.wait_for_selector(".grid .card")
    page.click(".grid .card")
    page.wait_for_selector(".episode")
    page.wait_for_load_state("networkidle")
    check("Titre 1x1" in page.inner_text(".episodes"), "titres d'épisodes TMDB")
    page.screenshot(path=f"{out}/08-anime.png", full_page=True)

    page.goto(base + "/#/library/1")
    page.wait_for_selector(".grid .card")
    page.click(".card[title='Brume']")
    page.wait_for_selector(".detail-actions")
    page.wait_for_load_state("networkidle")
    check(not page.locator("text=Identifier…").is_visible(), "outils d'admin masqués dans la vue normale")
    page.click("button[aria-label='Plus d’actions']")
    page.wait_for_selector(".more-list:not([hidden])")
    page.screenshot(path=f"{out}/09-menu-actions.png")
    page.click(".more-item:has-text('Informations sur le fichier')")
    page.wait_for_selector(".modal:has-text('HEVC')")
    page.screenshot(path=f"{out}/09b-infos-fichier.png")
    page.keyboard.press("Escape")
    page.click("button[aria-label='Plus d’actions']")
    page.click(".more-item:has-text('Identifier…')")
    page.wait_for_selector(".identify-hit")
    page.wait_for_load_state("networkidle")
    page.screenshot(path=f"{out}/10-identifier.png")
    page.keyboard.press("Escape")

    # --- collections, bonus, « À regarder » et playlists
    page.goto(base + "/#/library/1")
    page.wait_for_selector(".grid .card")
    page.click(".card[title='Le Grand Voyage']")
    page.wait_for_selector(".detail-actions")
    page.wait_for_load_state("networkidle")
    check(page.locator(".detail-actions a:has-text('Bande-annonce')").count() == 1, "bande-annonce locale")
    check(page.locator("section.block:has(h2:text-is('Bonus')) .card").count() == 2, "2 bonus")
    check(page.locator("text=Saga Démo").count() >= 1, "collection sur la fiche")
    page.click("button[aria-label='Ajouter à « À regarder »']")
    page.wait_for_selector("button[aria-pressed='true']")
    page.click("button[aria-label='Plus d’actions']")
    page.click(".more-item:has-text('Ajouter à une playlist')")
    page.fill(".modal input[placeholder='Nouvelle playlist…']", "Soirée")
    page.click(".modal button:has-text('Créer')")
    page.wait_for_selector(".modal-overlay", state="detached")
    page.screenshot(path=f"{out}/18a-fiche-bonus.png", full_page=True)
    page.goto(base + "/#/library/1?view=collections")
    page.wait_for_selector(".grid .card")
    check(page.locator(".grid .card").count() == 1, "1 collection")
    page.wait_for_function("[...document.images].every((i) => i.complete && i.naturalWidth)", timeout=15000)
    page.screenshot(path=f"{out}/18b-collections.png")
    page.click(".grid .card")
    page.wait_for_selector(".detail-title:text-is('Saga Démo')")
    page.wait_for_load_state("networkidle")
    check(page.locator(".grid .card").count() == 2, "2 films dans la saga")
    page.wait_for_function("[...document.images].every((i) => i.complete && i.naturalWidth)", timeout=15000)
    page.screenshot(path=f"{out}/18c-collection.png")
    page.goto(base + "/#/watchlist")
    page.wait_for_selector(".grid .card")
    check(page.locator(".grid .card").count() == 1, "1 élément à regarder")
    page.goto(base + "/#/playlists")
    page.click(".playlist-card")
    page.wait_for_selector(".episode")
    check(page.locator(".episode").count() == 1, "playlist avec 1 élément")
    page.screenshot(path=f"{out}/18d-playlist.png")

    # --- Découvrir : catalogues des plateformes et demandes via Seerr
    seerr = FakeSeerr()
    page.goto(base + "/#/admin/requests")
    page.wait_for_selector("text=Clé d’API de Seerr")
    page.fill("input[placeholder='http://192.168.1.20:5055']", seerr.url)
    page.fill("input[type=password]", seerr.key)
    page.click("button:has-text('Enregistrer et tester')")
    page.wait_for_selector(".toast:has-text('Connecté à Seerr')")
    page.wait_for_selector("text=Seerr est configuré")
    page.screenshot(path=f"{out}/19a-admin-demandes.png", full_page=True)
    page.goto(base + "/#/discover")
    page.wait_for_selector(".provider-title")
    check("Découvrir" in page.locator(".nav-link").all_inner_texts(), "onglet Découvrir")
    check(page.locator(".provider-title").count() == 1, "Netflix (Prime Video vide : rangée masquée)")
    page.wait_for_function("[...document.images].every((i) => i.complete && i.naturalWidth)", timeout=15000)
    check(page.locator(".dcard:has(.dbadge.available)").count() >= 2, "titres déjà disponibles signalés")
    page.screenshot(path=f"{out}/19b-decouvrir.png")
    page.click(".row:has-text('Sur Netflix') .dcard[title='Film Netflix']")
    page.wait_for_selector(".detail-title:text-is('Film Netflix')")
    page.wait_for_load_state("networkidle")
    page.click(".detail-actions button:has-text('Demander')")
    page.wait_for_selector(".request-status.pending")
    check(seerr.requests and seerr.requests[-1]["mediaId"] == 9101 and seerr.requests[-1]["user"] == 1, "demande transmise à Seerr")
    page.screenshot(path=f"{out}/19c-demande.png")
    page.goto(base + "/#/discover/title/tv/9201")
    page.wait_for_selector(".detail-actions button:has-text('Demander')")
    page.click(".detail-actions button:has-text('Demander')")
    page.wait_for_selector(".modal .check-row")
    page.locator(".modal .check-row input").nth(1).uncheck()
    page.screenshot(path=f"{out}/19d-saisons.png")
    page.click(".modal button.primary")
    page.wait_for_selector(".request-status.pending")
    check(seerr.requests[-1]["seasons"] == [1], "saison 1 seulement")
    page.goto(base + "/#/discover?provider=8&type=tv")
    page.wait_for_selector(".browse-title:has-text('Netflix')")
    check(page.locator(".grid .dcard").count() == 2, "catalogue séries Netflix")
    seerr.httpd.shutdown()

    # --- lecteur : MP4 en lecture directe -> refusé par ce Chromium (pas de H.264) -> bascule HLS
    page.goto(base + "/#/library/1")
    page.wait_for_selector(".grid .card")
    page.click(".card[title='Le Grand Voyage']")
    page.wait_for_selector(".detail-actions .btn.primary")
    page.click(".detail-actions .btn.primary")
    page.wait_for_function("document.querySelector('video') && document.querySelector('video').currentTime > 1.5", timeout=30000)
    check(page.evaluate("window.__hls.length > 0 && window.__hls.every(Boolean)"), "repli HLS après refus de la lecture directe")
    page.mouse.move(700, 400)
    page.click("button[aria-label='Qualité']")
    page.wait_for_selector(".menu-info")
    info = page.inner_text(".menu-info")
    check("Remux" in info, f"mode affiché dans le menu ({info})")
    page.screenshot(path=f"{out}/11-lecteur-mode.png")
    page.keyboard.press("Escape")

    # --- anime : sous-titres ASS affichés par le navigateur
    st, prefs = admin.call("PUT", "/api/me/prefs", {"sub_mode": "always"})
    st, lib = admin.call("GET", "/api/libraries/3/items")
    st, show = admin.call("GET", f"/api/items/{lib['items'][0]['id']}")
    ep1 = show["play_target"]["id"]
    for _ in range(100):  # vignettes d'aperçu préparées en tâche de fond
        if admin.call("GET", f"/api/items/{ep1}/trickplay")[1].get("ready"):
            break
        time.sleep(0.2)
    # fiche : choix des langues avant la lecture
    page.goto(base + f"/#/item/{ep1}")
    page.wait_for_selector(".track-chooser")
    page.wait_for_load_state("networkidle")
    href = page.get_attribute(".detail-actions a.btn.primary", "href")
    check("s=" in href and "s=0" not in href, f"sous-titres choisis transmis au lecteur ({href})")
    page.screenshot(path=f"{out}/12a-fiche-episode-pistes.png")
    # extraction lente simulée : préparation silencieuse (pas de fenêtre), la lecture attend les sous-titres
    _row = ctx.db.one("SELECT * FROM items WHERE id=?", (int(ep1),))
    shutil.rmtree(ctx.subtitles._dir(_row), ignore_errors=True)
    _orig = ctx.subtitles._ffmpeg

    def _slow(item, tracks, trick, tmp, job, fonts=True):
        time.sleep(3)
        return _orig(item, tracks, trick, tmp, job, fonts)
    ctx.subtitles._ffmpeg = _slow
    page.click(".detail-actions a.btn.primary")
    page.wait_for_selector(".player .controls")
    page.wait_for_timeout(1500)
    check(page.evaluate("document.querySelector('video').paused"), "la vidéo attend les sous-titres en silence")
    check(page.locator(".subs-wait").count() == 0, "aucune fenêtre de chargement des sous-titres")
    # ASS rendu par libass (WebAssembly) : panneau jaune positionné + police jointe au MKV
    page.wait_for_selector(".player .JASSUB canvas", state="attached", timeout=30000)
    page.wait_for_function("document.querySelector('video').currentTime > 2", timeout=30000)
    page.wait_for_timeout(800)
    ctx.subtitles._ffmpeg = _orig
    from PIL import Image
    import io
    shot = Image.open(io.BytesIO(page.screenshot(path=f"{out}/12d-ass-libass.png"))).convert("RGB")
    yellow = sum(1 for px in shot.getdata() if px[0] > 200 and px[1] > 200 and px[2] < 90)
    check(yellow > 300, f"panneau ASS dessiné par libass ({yellow} pixels jaunes)")
    check(page.locator(".sub-layer .sub-line").count() == 0, "pas de doublon en texte simplifié")
    # aperçu au survol de la barre, avec le chapitre
    page.mouse.move(700, 420)
    tl = page.locator(".timeline").bounding_box()
    page.mouse.move(tl["x"] + tl["width"] * (25 / 90), tl["y"] + tl["height"] / 2)
    page.wait_for_timeout(400)
    check(page.inner_text(".tl-preview-chapter") == "Opening", "chapitre affiché au survol")
    check(page.evaluate("!document.querySelector('.tl-preview-img').hidden"), "vignette d'aperçu au survol")
    check(page.locator(".tl-chapter").count() == 4, "repères de chapitres sur la barre")
    page.screenshot(path=f"{out}/12b-apercu-chapitre.png")
    page.mouse.move(700, 420)
    page.click("button[aria-label='Chapitres']")
    page.wait_for_selector(".chapter-panel .chapter-card.on")
    check(page.locator(".chapter-panel .chapter-card").count() == 5, "5 vignettes de chapitres")
    check(page.inner_text(".chapter-card.on .chapter-name") == "Prologue", "chapitre en cours en surbrillance")
    page.wait_for_function("[...document.querySelectorAll('.chapter-card img')].every((i) => i.complete && i.naturalWidth)", timeout=30000)
    page.screenshot(path=f"{out}/12c-chapitres.png")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.chapter-panel').hidden")
    page.wait_for_timeout(400)
    check(page.evaluate("document.querySelector('video').currentTime") >= 9.9, "saut au chapitre suivant (Opening)")
    page.mouse.move(700, 420)
    page.screenshot(path=f"{out}/12-lecteur-sous-titres.png")
    page.goto(base + "/#/")
    page.wait_for_selector(".hero-title")

    # --- profils : « Qui regarde ? » et code PIN
    st, kid = admin.call("POST", "/api/admin/users", {"username": "tom", "display_name": "Tom", "managed": True,
                                                      "pin": "1234", "max_age": 10, "avatar_color": "#4c9aff"})
    check(st == 200, f"profil enfant : {kid}")
    page.goto(base + "/#/profiles")
    page.wait_for_selector(".profile-tile")
    page.screenshot(path=f"{out}/16a-qui-regarde.png")
    page.click(".profile-tile:has-text('Tom')")
    page.wait_for_selector(".pin-keys")
    for d in "1234":
        page.click(f".pin-key:text-is('{d}')")
    page.wait_for_selector(".hero-title, .empty", timeout=15000)
    page.wait_for_load_state("networkidle")
    check(page.locator(".nav-link").all_inner_texts() == ["Accueil", "À regarder", "Découvrir", "Films", "Séries", "Animes"], "onglets du profil enfant")
    page.click(".nav-link:has-text('Animes')")
    page.wait_for_selector(".empty, .grid")
    check(page.locator(".grid .card").count() == 0, "animes classés 12 masqués pour l'enfant")
    page.screenshot(path=f"{out}/16b-profil-enfant.png")
    page.goto(base + "/#/profiles")
    page.wait_for_selector(".profile-tile")
    page.click(".profile-tile:has-text('colas')")
    page.fill(".modal input[type=password]", "motdepasse1")
    page.click(".modal button.primary")
    page.wait_for_selector(".hero-title")

    # --- partages réseau
    page.goto(base + "/#/admin/shares")
    page.wait_for_selector("text=Aucun partage réseau")
    page.click("text=+ Ajouter un partage")
    m = page.locator(".modal")
    m.locator("input[placeholder='192.168.1.5']").fill("192.168.1.5")
    m.locator("input").nth(1).fill("jellyfin")
    m.locator("input[type=password]").fill("mauvais")
    m.locator("text=Voir les partages du NAS").click()
    page.wait_for_selector(".modal .form-error:has-text('mot de passe refusé')")
    m.locator("input[type=password]").fill("secret")
    m.locator("text=Voir les partages du NAS").click()
    page.wait_for_selector(".share-pick")
    check(m.locator(".share-pick").count() == 3, "3 partages découverts (sans les partages $)")
    m.locator(".share-pick", has_text="Films").click()
    check(m.locator("input[placeholder='films']").input_value() == "films", "nom local proposé")
    page.screenshot(path=f"{out}/13-ajout-partage.png")
    m.locator("button.primary", has_text="Monter").click()
    page.wait_for_selector(".modal:has-text('Créer une bibliothèque avec ce dossier')")
    page.locator(".modal button", has_text="Créer une bibliothèque").click()
    page.wait_for_selector(".modal:has-text('Nouvelle bibliothèque')")
    check(page.locator(".modal .path-row input").input_value().endswith("/mnt/films"), "chemin du partage prérempli")
    page.screenshot(path=f"{out}/14-bibliotheque-depuis-partage.png")
    page.locator(".modal button", has_text="Annuler").click()
    page.wait_for_selector(".user-row:has-text('Monté')")
    page.screenshot(path=f"{out}/15-partages.png")

    # --- mobile
    mctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    mp = mctx.new_page()
    mp.goto(base + "/#/login")
    mp.fill("input[name=username]", "colas")
    mp.fill("input[name=password]", "motdepasse1")
    mp.click("button[type=submit]")
    mp.wait_for_selector(".profile-tile")  # plusieurs profils dans le foyer : « Qui regarde ? »
    mp.screenshot(path=f"{out}/17a-mobile-profils.png")
    mp.click(".profile-tile.current")
    mp.wait_for_selector(".hero-title")
    mp.goto(base + "/#/admin/libraries")
    mp.wait_for_selector(".user-row")
    mp.wait_for_load_state("networkidle")
    mp.screenshot(path=f"{out}/16-mobile-admin.png", full_page=True)
    sw = mp.evaluate("document.documentElement.scrollWidth")
    check(sw <= 390, f"pas de défilement horizontal sur mobile ({sw})")
    browser.close()

ctx.transcoder.stop_all()
server.shutdown()
shutil.rmtree(work, ignore_errors=True)
print("Erreurs console :", errors or "aucune")
print("Échecs :", failures or "aucun")
sys.exit(1 if failures or errors else 0)
