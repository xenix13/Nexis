"""Browser smoke test: drives the real UI against the fake Plex and saves screenshots.

python3 tests/ui_smoke.py <output_dir>
"""
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright  # noqa: E402

from seance.api import Ctx, build_app  # noqa: E402
from seance.web import Server, make_handler  # noqa: E402
from tests.fakeplex import TOKEN, FakePlex  # noqa: E402
from tests.test_api import STATIC, Client  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "shots"
os.makedirs(out, exist_ok=True)
plex = FakePlex()
tmp = tempfile.mkdtemp()
ctx = Ctx(tmp, STATIC)
setup_code = ctx.setup_code
server = Server(("127.0.0.1", 0), make_handler(build_app(ctx)))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"
errors = []
failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("ÉCHEC :", msg)


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

    # --- setup wizard through the UI
    page.goto(base + "/")
    page.wait_for_selector("text=Première installation")
    page.screenshot(path=f"{out}/01-installation.png", full_page=True)
    inputs = page.locator("form input")
    page.fill("input[placeholder='Ex. 4F2A9C1B']", setup_code)
    page.fill("input[autocomplete='username']", "colas")
    page.fill("input[placeholder='Ton prénom']", "Colas")
    pw_inputs = page.locator("input[autocomplete='new-password']")
    pw_inputs.nth(0).fill("motdepasse1")
    pw_inputs.nth(1).fill("motdepasse1")
    page.fill("input[placeholder='http://192.168.1.10:32400']", plex.url)
    page.fill("input[placeholder='Colle ton X-Plex-Token']", TOKEN)
    page.click("text=Tester la connexion")
    page.wait_for_selector("text=Connecté à « Plex de test »")
    page.click("text=Terminer l’installation")
    page.wait_for_selector(".hero-title")
    check(page.locator(".nav-link").count() == 4, "4 liens de navigation (Accueil + 3 bibliothèques vidéo)")
    page.wait_for_timeout(600)
    page.screenshot(path=f"{out}/02-accueil-vide.png")

    # --- create a second user via the admin UI
    page.goto(base + "/#/admin/users")
    page.click("text=+ Ajouter")
    modal = page.locator(".modal")
    modal.locator("input").nth(0).fill("lea")
    modal.locator("input").nth(1).fill("Léa")
    modal.locator("input[type=password]").fill("motdepasse2")
    modal.locator(".toggle", has_text="Accès à toutes").click()
    modal.locator(".toggle", has_text="Films").click()
    modal.locator(".toggle", has_text="Animes").click()
    page.screenshot(path=f"{out}/03-admin-nouvel-utilisateur.png")
    modal.locator("button.primary", has_text="Créer").click()
    try:
        page.wait_for_selector("text=@lea", timeout=8000)
    except Exception:
        print("Modal :", page.locator(".modal").inner_text() if page.locator(".modal").count() else "fermée")
        page.screenshot(path=f"{out}/debug-admin.png")
        raise
    page.screenshot(path=f"{out}/04-admin-utilisateurs.png")

    # --- seed some activity for Léa through the API
    lea = Client(base)
    lea.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})
    st, p = lea.call("POST", "/api/play", {"id": "102"})
    lea.call("POST", "/api/progress", {"id": "102", "position": 2_400_000, "duration": 6_300_000, "stream": p["stream"],
                                       "state": "stopped"})
    lea.call("POST", "/api/items/3111/watched", {"watched": True})
    lea.call("PUT", "/api/me/prefs", {"accent": "#a78bfa", "sub_mode": "always"})

    # --- log in as Léa in a fresh context
    ctx2 = browser.new_context(viewport={"width": 1440, "height": 900}, bypass_csp=True)  # needed for wait_for_function
    page2 = ctx2.new_page()
    page2.on("pageerror", lambda e: errors.append(f"[pageerror lea] {e}"))
    page2.goto(base + "/#/login")
    page2.fill("input[name=username]", "lea")
    page2.fill("input[name=password]", "mauvais")
    page2.click("button[type=submit]")
    page2.wait_for_selector("text=Identifiant ou mot de passe incorrect")
    page2.screenshot(path=f"{out}/05-connexion-erreur.png")
    page2.fill("input[name=password]", "motdepasse2")
    page2.click("button[type=submit]")
    page2.wait_for_selector(".hero-title")
    page2.wait_for_load_state("networkidle")
    nav = page2.locator(".nav-link").all_inner_texts()
    check(nav == ["Accueil", "Films", "Animes"], f"Léa ne voit que Films et Animes (vu : {nav})")
    check(page2.locator(".wide-row .card").count() == 2, "Reprendre (film) + À suivre (épisode 2)")
    page2.screenshot(path=f"{out}/06-accueil-lea.png")
    page2.screenshot(path=f"{out}/06b-accueil-lea-complet.png", full_page=True)

    page2.click(".nav-link:has-text('Films')")
    page2.wait_for_selector(".grid .card")
    page2.wait_for_load_state("networkidle")
    page2.screenshot(path=f"{out}/07-bibliotheque.png")

    page2.goto(base + "/#/item/301")
    page2.wait_for_selector(".episode")
    page2.wait_for_load_state("networkidle")
    check("Lire S01E02" in page2.inner_text(".detail-actions"), "Bouton « Lire S01E02 » sur la série")
    page2.screenshot(path=f"{out}/08-serie.png", full_page=True)

    page2.goto(base + "/#/item/102")
    page2.wait_for_selector(".detail-actions")
    check("Reprendre à 40:00" in page2.inner_text(".detail-actions"), "Reprise à 40:00 sur le film")
    page2.screenshot(path=f"{out}/09-film.png")

    page2.goto(base + "/#/settings")
    page2.wait_for_selector(".panel")
    page2.screenshot(path=f"{out}/10-reglages.png", full_page=True)

    page2.goto(base + "/#/history")
    page2.wait_for_selector(".history-item")
    page2.screenshot(path=f"{out}/11-historique.png")

    page2.goto(base + "/#/search?q=nuit")
    page2.wait_for_selector(".grid .card")
    page2.screenshot(path=f"{out}/12-recherche.png")

    # --- player. No hls.js here (no internet), so a stub Hls plays a local WebM
    # after checking that the proxied HLS playlist is reachable from the browser.
    native_hls = page2.evaluate("document.createElement('video').canPlayType('application/vnd.apple.mpegurl')")
    print("HLS natif dans ce Chromium :", repr(native_hls))
    stub = """
    window.__hlsChecks = [];
    window.Hls = class {
      static isSupported() { return true; }
      static Events = { ERROR: 'error', MANIFEST_PARSED: 'parsed' };
      static ErrorTypes = { MEDIA_ERROR: 'media' };
      constructor() { this.h = {}; }
      on(ev, fn) { this.h[ev] = fn; }
      loadSource(u) { fetch(u).then(r => r.text()).then(t => window.__hlsChecks.push(t.startsWith('#EXTM3U') && !t.includes('X-Plex-Token'))); }
      attachMedia(v) { v.src = '/__test.webm'; v.addEventListener('loadedmetadata', () => this.h.parsed && this.h.parsed(), { once: true }); }
      destroy() {} recoverMediaError() {}
    };"""
    video_path = os.path.join(os.path.dirname(os.path.abspath(out)), "test.webm")
    ctx2.route("**/vendor/hls.min.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=stub))
    ctx2.route("**/__test.webm", lambda r: r.fulfill(status=200, content_type="video/webm", path=video_path))
    page2.goto(base + "/")
    page2.wait_for_selector(".hero-title")
    page2.goto(base + "/#/play/3111?t=1000")
    page2.wait_for_selector(".player .controls")
    page2.wait_for_function("document.querySelector('video').currentTime > 1.6", timeout=20000)
    page2.mouse.move(700, 400)
    check(page2.evaluate("window.__hlsChecks.every(Boolean) && window.__hlsChecks.length > 0"), "Playlist HLS servie et nettoyée")
    check("Bonjour tout le monde" in page2.inner_text(".sub-layer"), "Sous-titres SRT affichés en surimpression")
    check(not page2.evaluate("document.querySelector('.tl-intro').hidden"), "Marqueur d'intro sur la timeline")
    page2.screenshot(path=f"{out}/13-lecteur-sous-titres.png")
    page2.click("button[aria-label='Audio et sous-titres']")
    page2.wait_for_selector(".player-menu .menu-col")
    page2.screenshot(path=f"{out}/14-lecteur-menu.png")
    page2.keyboard.press("Escape")
    page2.goto(base + "/#/play/3111?t=40000")
    page2.wait_for_selector(".skip-btn:not([hidden])", timeout=20000)
    page2.mouse.move(700, 420)
    page2.screenshot(path=f"{out}/15-lecteur-passer-intro.png")
    page2.click(".skip-btn")
    page2.wait_for_function("document.querySelector('.skip-btn').hidden", timeout=20000)
    page2.wait_for_timeout(1500)
    t = page2.inner_text(".time")
    check(t.startswith("2:0") or t.startswith("2:1"), f"Après « Passer l'intro », la lecture reprend vers 2:00 (affiché {t})")
    # near the end: next-episode card
    page2.goto(base + "/#/play/3111?t=1405000")
    page2.wait_for_selector(".next-card:not([hidden])", timeout=20000)
    page2.mouse.move(700, 420)
    page2.screenshot(path=f"{out}/16-lecteur-episode-suivant.png")
    page2.wait_for_function("location.hash === '#/play/3112'", timeout=20000)
    page2.wait_for_function("document.querySelector('video').currentTime > 5", timeout=20000)
    page2.goto(base + "/#/")
    page2.wait_for_selector(".hero-title")
    page2.wait_for_timeout(800)
    st, prog = lea.call("GET", "/api/items/3112")
    check(prog["position"] > 0, f"La progression de l'épisode 2 est enregistrée ({prog['position']} ms)")
    # resume position must survive a far seek (stream restart) and leaving the player
    page2.goto(base + "/#/play/104?t=600000")
    page2.wait_for_function("document.querySelector('video').currentTime > 2", timeout=20000)
    page2.evaluate("document.querySelector('.timeline').dispatchEvent(new PointerEvent('pointerdown', {clientX: 0, pointerId: 1}))")
    tl = page2.locator(".timeline").bounding_box()
    page2.mouse.move(tl["x"] + tl["width"] * 0.5, tl["y"] + tl["height"] / 2)
    page2.mouse.down()
    page2.mouse.up()
    page2.wait_for_function("document.querySelector('.time').textContent.startsWith('0:5') && document.querySelector('video').currentTime > 3", timeout=20000)
    page2.goto(base + "/#/")
    page2.wait_for_selector(".hero-title")
    page2.wait_for_timeout(800)
    st, prog = lea.call("GET", "/api/items/104")
    check(3_440_000 < prog["position"] < 3_480_000, f"Reprise conservée après un saut lointain ({prog['position']} ms)")

    # --- mobile layout
    m = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    mp = m.new_page()
    mp.goto(base + "/#/login")
    mp.fill("input[name=username]", "lea")
    mp.fill("input[name=password]", "motdepasse2")
    mp.click("button[type=submit]")
    mp.wait_for_selector(".hero-title")
    mp.wait_for_load_state("networkidle")
    mp.screenshot(path=f"{out}/17-mobile-accueil.png")
    mp.goto(base + "/#/item/301")
    mp.wait_for_selector(".episode")
    mp.wait_for_load_state("networkidle")
    mp.screenshot(path=f"{out}/18-mobile-serie.png", full_page=True)
    sw = mp.evaluate("document.documentElement.scrollWidth")
    check(sw <= 390, f"Pas de défilement horizontal sur mobile (largeur {sw})")
    browser.close()

server.shutdown()
plex.close()
shutil.rmtree(tmp, ignore_errors=True)
noise = [e for e in errors if "hls.min.js" not in e and "404" not in e]
print("\nErreurs console :", *noise or ["aucune"], sep="\n  ")
print("Échecs :", failures or "aucun")
sys.exit(1 if failures or noise else 0)
