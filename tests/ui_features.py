"""Parcours navigateur des nouveautés (Playwright) + captures : chapitres en vignettes, tâches planifiées,
TV en direct, « Regarder ensemble ».

python3 tests/ui_features.py <dossier_captures> <video_test.webm>
"""
import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.test_api import STATIC, TMDB, Client, media_root, wait_scan  # noqa: E402,F401
from tests.ui_smoke_stub import STUB, serve_webm  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

from seance.api import Ctx, build_app  # noqa: E402
from seance.web import Server, make_handler  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "captures"
webm = sys.argv[2]
os.makedirs(out, exist_ok=True)
work = tempfile.mkdtemp()
media = os.path.join(work, "media")
shutil.copytree(media_root(), media)
from seance import scanner  # noqa: E402
scanner.RECENT_SECONDS = 0
ctx = Ctx(os.path.join(work, "data"), STATIC)
server = Server(("127.0.0.1", 0), make_handler(build_app(ctx)))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_address[1]}"
ctx.tasks.start()
admin = Client(base)
assert admin.call("POST", "/api/setup", {"username": "colas", "password": "motdepasse1", "display_name": "Colas"},
                  headers={"X-Setup-Code": ctx.setup_code})[0] == 200
admin.call("PUT", "/api/admin/settings", {"tmdb_key": TMDB.key})
for name, kind, sub in (("Films", "movie", "films"), ("Séries", "show", "series"), ("Animes", "show", "animes")):
    wait_scan(admin)
    admin.call("POST", "/api/admin/libraries", {"name": name, "kind": kind, "paths": [os.path.join(media, sub)]})
wait_scan(admin)
admin.call("POST", "/api/admin/users", {"username": "lea", "password": "motdepasse2", "display_name": "Léa",
                                        "avatar_color": "#4c9aff"})
lea = Client(base)
lea.call("POST", "/api/login", {"username": "lea", "password": "motdepasse2"})

# TV en direct : liste M3U locale (fichiers vidéo de test) + guide XMLTV
films = os.path.join(media, "films")
mp4 = os.path.join(films, "Le Grand Voyage (2019)", "Le Grand Voyage (2019).mp4")
now = time.time()


def t(sec):
    return time.strftime("%Y%m%d%H%M%S +0000", time.gmtime(sec))


chans = [("tf", "Voyage TV", "Généralistes"), ("fr2", "Deux", "Généralistes"), ("arte", "Culture+", "Généralistes"),
         ("info", "Info 24", "Info"), ("sport", "Sport Max", "Sport"), ("ciné", "Ciné Club", "Cinéma"),
         ("kids", "Petits Loups", "Jeunesse"), ("docs", "Planète Docs", "Découverte")]
shows = ["Le journal", "La météo", "Série policière", "Documentaire animalier", "Magazine", "Film : Le Grand Voyage",
         "Dessins animés", "Débat", "Football : finale", "Jeu télévisé"]
xml = ['<?xml version="1.0" encoding="UTF-8"?><tv>']
for i, (cid, name, _) in enumerate(chans):
    xml.append(f'<channel id="{cid}"><display-name>{name}</display-name></channel>')
    start = now - 1500 - i * 300
    for k in range(10):
        dur = 1800 + ((i * 7 + k * 13) % 5) * 900
        xml.append(f'<programme start="{t(start)}" stop="{t(start + dur)}" channel="{cid}"><title>{shows[(i + k) % len(shows)]}</title>'
                   f'<desc>Résumé de l’émission numéro {k + 1}.</desc><category>Divers</category></programme>')
        start += dur
xml.append("</tv>")
with open(os.path.join(work, "guide.xml"), "w", encoding="utf-8") as f:
    f.write("".join(xml))
with open(os.path.join(work, "tv.m3u"), "w", encoding="utf-8") as f:
    f.write(f'#EXTM3U url-tvg="{os.path.join(work, "guide.xml")}"\n')
    for n, (cid, name, grp) in enumerate(chans, 1):
        f.write(f'#EXTINF:-1 tvg-id="{cid}" tvg-chno="{n}" group-title="{grp}",{name}\n{mp4}\n')
st, r = admin.call("POST", "/api/admin/live/sources", {"name": "Box TV", "m3u_url": os.path.join(work, "tv.m3u")})
assert st == 200 and r["sources"][0]["channels"] == 8, r

errors, failures = [], []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print("ÉCHEC :", msg)


def cookies(client):
    return [{"name": c.name, "value": c.value, "url": base} for c in client.jar]


def browser_ctx(browser, client, mobile=False):
    bctx = browser.new_context(viewport={"width": 390, "height": 844} if mobile else {"width": 1440, "height": 900},
                               bypass_csp=True, device_scale_factor=2 if mobile else 1)
    bctx.route("**/vendor/hls.min.js", lambda r: r.fulfill(status=200, content_type="text/javascript", body=STUB))
    bctx.route("**/__test.webm", serve_webm(webm))
    bctx.add_cookies(cookies(client))
    page = bctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
    page.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" and "404" not in m.text
            and "Failed to load resource" not in m.text else None)
    page.on("response", lambda r: errors.append(f"[{r.status}] {r.url}") if r.status >= 500 else None)
    return bctx, page


kaze = ctx.db.one("SELECT id FROM items WHERE key LIKE '%Kaze no Michi - 01%'")["id"]
voyage = ctx.db.one("SELECT id FROM items WHERE type='movie' AND title='Le Grand Voyage'")["id"]

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    bctx, page = browser_ctx(browser, admin)

    # --- Seerr : plus que la connexion à Seerr
    page.goto(base + "/#/admin/requests")
    page.wait_for_selector("text=Clé d’API de Seerr")
    check(page.locator("text=Relier Seerr").count() == 0, "section « Relier Seerr à Nexis » retirée")
    page.screenshot(path=f"{out}/01-admin-seerr.png", full_page=True)

    # --- chapitres : fiche et lecteur
    page.goto(base + f"/#/item/{kaze}")
    page.wait_for_selector(".block:has-text('Chapitres') .chapter-card")
    check(page.locator(".block .chapter-card").count() == 5, "chapitres sur la fiche")
    page.wait_for_function("[...document.querySelectorAll('.chapter-card img')].every((i) => i.complete && i.naturalWidth)", timeout=60000)
    page.locator(".block:has-text('Chapitres')").scroll_into_view_if_needed()
    page.screenshot(path=f"{out}/02-fiche-chapitres.png")
    page.click(".block .chapter-card:has-text('Partie A')")
    try:
        page.wait_for_function("document.querySelector('video') && document.querySelector('video').currentTime > 39", timeout=60000)
    except Exception:
        page.screenshot(path=f"{out}/debug.png")
        print(page.evaluate("(() => { const v = document.querySelector('video'); return v ? [v.currentTime, v.paused, v.src, v.readyState, location.hash] : null; })()"))
        raise
    check(True, "lecture depuis un chapitre de la fiche")
    page.mouse.move(700, 420)
    page.click("button[aria-label='Chapitres']")
    page.wait_for_selector(".chapter-panel .chapter-card.on")
    page.wait_for_timeout(500)
    check(page.inner_text(".chapter-card.on .chapter-name") in ("Opening", "Partie A"), "chapitre en cours en surbrillance")
    page.locator(".chapter-panel .chapter-card:has-text('Ending')").focus()
    page.screenshot(path=f"{out}/03-lecteur-chapitres.png")
    page.keyboard.press("ArrowLeft")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelector('.chapter-panel').hidden")
    page.wait_for_timeout(500)
    tnow = page.evaluate("document.querySelector('video').currentTime")
    check(40 <= tnow < 50, f"saut au chapitre choisi au clavier (Partie A) : {tnow}")

    # --- fin d'épisode : compte à rebours réglable (5 s par défaut), annulable
    ep1 = page.evaluate("location.hash")
    page.evaluate("document.querySelector('video').dispatchEvent(new Event('ended'))")
    page.wait_for_selector(".next-card.end:not([hidden])")
    check("dans 5 s" in page.inner_text(".next-card.end") or "dans 4 s" in page.inner_text(".next-card.end"),
          "compte à rebours de 5 s à la fin de l'épisode")
    page.screenshot(path=f"{out}/03b-fin-episode.png")
    page.click(".next-card.end >> text=Annuler")
    page.wait_for_timeout(6500)
    check(page.evaluate("location.hash") == ep1 and "dans" not in page.inner_text(".next-card"),
          "« Annuler » arrête le compte à rebours")
    page.click(".next-card >> text=Fermer")
    page.evaluate("document.querySelector('video').dispatchEvent(new Event('ended'))")
    page.wait_for_function(f"location.hash !== {ep1!r}", timeout=9000)
    check("/play/" in page.evaluate("location.hash"), "épisode suivant lancé à la fin du compte à rebours")

    # --- lecteur réduit : la lecture continue pendant la navigation
    ep2 = page.evaluate("location.hash")
    page.wait_for_function("document.querySelector('video') && document.querySelector('video').currentTime > 1", timeout=60000)
    page.mouse.move(700, 420)
    page.click("button[aria-label='Réduire le lecteur']")
    page.wait_for_selector("#mini-dock .player.mini")
    page.goto(base + "/#/search")
    page.wait_for_timeout(300)
    t1 = page.evaluate("document.querySelector('#mini-dock video').currentTime")
    page.wait_for_timeout(2000)
    t2 = page.evaluate("document.querySelector('#mini-dock video').currentTime")
    check(t2 > t1 and page.locator("#app .player").count() == 0, f"lecture continue en réduit ({t1:.1f} -> {t2:.1f})")
    page.screenshot(path=f"{out}/03c-lecteur-reduit.png")
    page.hover("#mini-dock .player")
    page.click("button[aria-label='Agrandir le lecteur']")
    page.wait_for_selector("#app .player:not(.mini)")
    t3 = page.evaluate("document.querySelector('video').currentTime")
    check(page.evaluate("location.hash") == ep2 and t3 >= t2 and page.locator("video").count() == 1,
          f"agrandi sans recharger ({t3:.1f})")
    page.mouse.move(700, 420)
    page.click("button[aria-label='Réduire le lecteur']")
    page.wait_for_selector("#mini-dock .player.mini")
    page.hover("#mini-dock .player")
    page.click("button[aria-label='Fermer le lecteur']")
    page.wait_for_function("!document.querySelector('#mini-dock .player')")
    check(True, "fermeture du lecteur réduit")

    # --- « marquer comme vu » depuis Reprendre et à suivre, puis « Annuler »
    ctx.db.run("UPDATE items SET duration_ms=7200000 WHERE id=?", (voyage,))  # fichiers de test : 12 s
    st, p = lea.call("POST", "/api/play", {"id": voyage})
    lea.call("POST", "/api/progress", {"id": voyage, "position": 600000, "duration": 7200000, "stream": p["stream"],
                                       "state": "playing"})
    lea.call("POST", "/api/stop", {"stream": p["stream"]})
    lctx, lpage = browser_ctx(browser, lea)
    lpage.goto(base + "/#/")
    slot = ".wide-row .card-slot:has-text('Le Grand Voyage')"
    lpage.wait_for_selector(slot)
    lpage.hover(slot)
    lpage.screenshot(path=f"{out}/03d-marquer-vu.png")
    lpage.click(f"{slot} .card-watch")
    lpage.wait_for_selector(".toast:has-text('marqué comme vu')")
    lpage.wait_for_function(f"!document.querySelector(\".wide-row .card-slot a[href*='/play/{voyage}']\")")
    check(lea.call("GET", f"/api/items/{voyage}")[1]["watched"], "film marqué vu depuis l'accueil")
    lpage.click(".toast-action")
    lpage.wait_for_selector(slot)
    d = lea.call("GET", f"/api/items/{voyage}")[1]
    check(not d["watched"] and d["position"] == 600000, f"« Annuler » remet la reprise ({d['watched']}, {d['position']})")
    lctx.close()

    # --- ordre de l'accueil et de la barre du haut (Réglages → Accueil / Barre du haut)
    lctx, lpage = browser_ctx(browser, lea)
    lpage.goto(base + "/#/settings")
    lpage.wait_for_selector(".layout-list")
    home_list = ".panel:has(h2:text-is('Accueil')) .layout-list"
    up = f"{home_list} button[aria-label='Monter Films']"
    while lpage.is_enabled(up):  # jusqu'en haut de la liste
        lpage.click(up)
        lpage.wait_for_timeout(250)
    lpage.click(f"{home_list} .layout-item[data-key='watchlist'] .toggle")
    nav_list = ".panel:has(h2:text-is('Barre du haut')) .layout-list"
    lpage.click(f"{nav_list} .layout-item[data-key='watchlist'] .toggle")
    lpage.wait_for_timeout(400)
    lpage.click(f"{nav_list} button[aria-label='Monter TV en direct']")
    lpage.wait_for_timeout(400)
    links = lpage.eval_on_selector_all("header .nav .nav-link", "els => els.map((e) => e.textContent)")
    check("À regarder" not in links and links.index("TV en direct") < links.index("Animes"),
          f"barre du haut réordonnée sans recharger : {links}")
    lpage.locator(".panel:has(h2:text-is('Accueil'))").scroll_into_view_if_needed()
    lpage.screenshot(path=f"{out}/03e-reglages-ordre.png")
    lpage.goto(base + "/#/")
    lpage.wait_for_selector(".row-title")
    rows = lpage.eval_on_selector_all(".content .row-title", "els => els.map((e) => e.textContent)")
    check(rows and rows[0].startswith("Ajoutés récemment · Films") and "À regarder" not in rows, f"accueil réordonné : {rows}")
    lea.call("PUT", "/api/me/prefs", {"home_order": [], "home_hidden": [], "nav_order": [], "nav_hidden": []})
    # --- fiche : titres similaires (genres, réalisateur, acteurs)
    lpage.goto(base + f"/#/item/{voyage}")
    lpage.wait_for_selector("section.block h2:text-is('Films similaires')")
    check(lpage.locator("section.block:has(h2:text-is('Films similaires')) .card:has-text('Brume')").count() == 1,
          "Brume dans les films similaires au Grand Voyage")
    lctx.close()

    # --- tâches planifiées
    page.goto(base + "/#/admin/tasks")
    page.wait_for_selector(".task-row")
    check(page.locator(".task-row").count() == 7, "7 tâches")
    page.screenshot(path=f"{out}/04-taches.png", full_page=True)
    page.click(".task-row:has-text('Détecter les intros') button:has-text('Réglages')")
    page.wait_for_selector(".modal:has-text('Planification')")
    page.screenshot(path=f"{out}/05-tache-reglages.png")
    page.select_option(".modal select >> nth=0", "weekly")
    page.locator(".modal input[type=time]").fill("02:30")
    page.click(".modal button.primary")
    page.wait_for_selector(".task-row:has-text('Détecter les intros') :text('Chaque dimanche à 02:30')")
    page.click(".task-row:has-text('Extraire les images') button:has-text('Lancer')")
    page.wait_for_selector(".task-row:has-text('Extraire les images') :text('Terminée')", timeout=60000)
    page.screenshot(path=f"{out}/06-tache-terminee.png", full_page=True)

    # --- TV en direct
    page.goto(base + "/#/admin/live")
    page.wait_for_selector(".user-row:has-text('Box TV')")
    page.screenshot(path=f"{out}/07-admin-tv.png", full_page=True)
    page.goto(base + "/#/live")
    page.wait_for_selector(".ch-card")
    check("TV en direct" in page.locator(".nav-link").all_inner_texts(), "onglet TV en direct")
    check(page.locator(".ch-card").count() == 8, "8 chaînes")
    page.locator(".ch-card:has-text('Deux') .fav").click()
    page.wait_for_selector(".ch-card:has-text('Deux') .fav.on")
    page.screenshot(path=f"{out}/08-tv-chaines.png")
    page.goto(base + "/#/live?view=guide")
    page.wait_for_selector(".guide-prog.live")
    page.screenshot(path=f"{out}/09-tv-guide.png")
    page.locator(".guide-prog.live").first.click()
    page.wait_for_selector(".modal button:has-text('Regarder')")
    page.screenshot(path=f"{out}/10-tv-programme.png")
    page.click(".modal button:has-text('Regarder')")
    page.wait_for_selector(".live-player")
    page.wait_for_function("document.querySelector('video') && document.querySelector('video').currentTime > 1", timeout=60000)
    page.mouse.move(700, 420)
    page.wait_for_timeout(300)
    page.screenshot(path=f"{out}/11-tv-lecteur.png")
    page.keyboard.press("l")
    page.wait_for_selector(".live-side-item.on")
    page.wait_for_timeout(300)
    page.screenshot(path=f"{out}/12-tv-zapping.png")
    page.keyboard.press("Escape")
    page.keyboard.press("ArrowUp")
    page.wait_for_function("location.hash !== '' && document.querySelector('.player-title strong') && document.querySelector('.player-title strong').textContent.includes('2 · Deux')", timeout=30000)
    check(True, "chaîne suivante")
    page.goto(base + "/#/")

    # --- regarder ensemble : Colas crée un groupe, Léa le rejoint
    page.goto(base + f"/#/item/{voyage}")
    page.click(".detail-actions button[aria-label='Regarder ensemble']")
    page.wait_for_selector(".modal:has-text('Regarder ensemble')")
    page.fill(".modal input", "Soirée cinéma")
    page.screenshot(path=f"{out}/13-ensemble-creer.png")
    page.click(".modal button:has-text('Créer et lancer')")
    page.wait_for_function("location.hash.includes('group=')")
    page.wait_for_function("document.querySelector('video') && document.querySelector('video').currentTime > 1", timeout=60000)
    bctx2, page2 = browser_ctx(browser, lea)
    page2.goto(base + "/#/")
    page2.wait_for_selector(".group-card:has-text('Soirée cinéma')")
    check(page2.locator(".group-pill").count() == 1, "raccourci « Colas regarde » dans la barre")
    page2.screenshot(path=f"{out}/14-ensemble-accueil.png")
    page2.click(".group-card a:has-text('Rejoindre')")
    page2.wait_for_function("location.hash.includes('group=')")
    page2.wait_for_function("document.querySelector('video') && document.querySelector('video').currentTime > 1", timeout=60000)
    page2.wait_for_timeout(1500)
    t1, t2 = page.evaluate("document.querySelector('video').currentTime"), page2.evaluate("document.querySelector('video').currentTime")
    g = next(iter(ctx.together.groups.values()))
    check(abs(t1 - t2) < 2.5, f"positions synchronisées ({t1:.1f} / {t2:.1f}, serveur {g.position_now() / 1000:.1f}, "
          f"{'lecture' if g.playing else 'pause'}, attente {g.waiting})")
    # Colas met en pause : Léa est mise en pause aussi
    page.mouse.move(700, 420)
    page.keyboard.press(" ")
    page2.wait_for_function("document.querySelector('video').paused", timeout=10000)
    page2.wait_for_selector(".toast:has-text('Colas a mis en pause')")
    page2.mouse.move(700, 420)
    page2.screenshot(path=f"{out}/15-ensemble-pause.png")
    # Léa saute plus loin : Colas suit
    page2.keyboard.press(" ")
    page.wait_for_function("!document.querySelector('video').paused", timeout=10000)
    page2.keyboard.press("ArrowRight")
    page2.keyboard.press("ArrowRight")
    for _ in range(12):  # le groupe se recale en quelques secondes au plus
        page2.wait_for_timeout(500)
        t1, t2 = page.evaluate("document.querySelector('video').currentTime"), page2.evaluate("document.querySelector('video').currentTime")
        if abs(t1 - t2) < 1 and t2 > 18:
            break
    check(abs(t1 - t2) < 1 and t2 > 18, f"saut partagé ({t1:.1f} / {t2:.1f})")
    page.mouse.move(700, 420)
    page.click("button[aria-label='Regarder ensemble']")
    page.wait_for_selector(".group-member:has-text('Léa')")
    page.screenshot(path=f"{out}/16-ensemble-membres.png")
    page.keyboard.press("Escape")
    bctx2.close()

    # --- mobile
    bctx3, page3 = browser_ctx(browser, lea, mobile=True)
    page3.goto(base + "/#/live")
    page3.wait_for_selector(".ch-card")
    page3.screenshot(path=f"{out}/17-mobile-tv.png")
    page3.goto(base + f"/#/item/{kaze}")
    page3.wait_for_selector(".block .chapter-card")
    page3.locator(".block:has-text('Chapitres')").scroll_into_view_if_needed()
    page3.wait_for_timeout(500)
    page3.screenshot(path=f"{out}/18-mobile-chapitres.png")
    bctx3.close()
    browser.close()

ctx.tasks.stop()
ctx.live.stop_all()
print("Erreurs console :", errors or "aucune")
print("Échecs :", failures or "aucun")
server.shutdown()
shutil.rmtree(work, ignore_errors=True)
sys.exit(1 if failures or errors else 0)
