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
from tests.fixtures import FakeTMDB  # noqa: E402

TMDB = FakeTMDB()
os.environ["SEANCE_TMDB_API"] = TMDB.url
os.environ["SEANCE_TMDB_IMG"] = TMDB.url + "/img"

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
    bctx.route("**/__test.webm", lambda r: r.fulfill(status=200, content_type="video/webm", path=webm))
    page = bctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" and "404" not in m.text else None)

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
    check(page.locator(".nav-link").all_inner_texts() == ["Accueil", "Films", "Séries", "Animes"], "onglets des bibliothèques")

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
    page.wait_for_selector(".admin-tools")
    page.wait_for_load_state("networkidle")
    check("HEVC" in page.inner_text(".admin-tools"), "infos techniques pour l'admin")
    page.screenshot(path=f"{out}/09-film-admin.png")
    page.click("text=Identifier…")
    page.wait_for_selector(".identify-hit")
    page.wait_for_load_state("networkidle")
    page.screenshot(path=f"{out}/10-identifier.png")
    page.keyboard.press("Escape")

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
    page.goto(base + "/")
    page.wait_for_selector(".hero-title")
    page.goto(base + f"/#/play/{ep1}?t=500")
    page.wait_for_function("document.querySelector('.sub-layer').textContent.includes('Le vent se lève')", timeout=30000)
    page.mouse.move(700, 420)
    page.screenshot(path=f"{out}/12-lecteur-sous-titres.png")
    page.goto(base + "/#/")
    page.wait_for_selector(".hero-title")

    # --- mobile
    mctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    mp = mctx.new_page()
    mp.goto(base + "/#/login")
    mp.fill("input[name=username]", "colas")
    mp.fill("input[name=password]", "motdepasse1")
    mp.click("button[type=submit]")
    mp.wait_for_selector(".hero-title")
    mp.goto(base + "/#/admin/libraries")
    mp.wait_for_selector(".user-row")
    mp.wait_for_load_state("networkidle")
    mp.screenshot(path=f"{out}/13-mobile-admin.png", full_page=True)
    sw = mp.evaluate("document.documentElement.scrollWidth")
    check(sw <= 390, f"pas de défilement horizontal sur mobile ({sw})")
    browser.close()

ctx.transcoder.stop_all()
server.shutdown()
shutil.rmtree(work, ignore_errors=True)
print("Erreurs console :", errors or "aucune")
print("Échecs :", failures or "aucun")
sys.exit(1 if failures or errors else 0)
